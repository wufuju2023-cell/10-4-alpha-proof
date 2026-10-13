"""官方式离线专家迭代 learner。

损失（对齐官方 A5）：
- policy：对“搜索选中动作”做交叉熵（teacher-forcing NLL，prompt 段掩码 -100）；
- value：64 桶交叉熵，目标 = 剩余步数 d(s) 的两热分布，权重 value_coef=1e-3；
- 样本：replay 的证明 + 反证都训练；超时默认剔除（include_timeout=True 可开）；
- SFT 样本只贡献 policy 项（无 value target）。

注：官方 batch=4096 / 1M steps 为规模设定；本实现 batch 可配置，micro 分块做梯度累积。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from alphaproof.config import TrainConfig
from alphaproof.data.events import Transition

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]

EncodeFn = Callable[..., Any]  # (prompt, action) -> (input_ids, labels, prompt_len[, attention_mask])


def make_hf_encode_fn(tokenizer, device: str = "cpu") -> EncodeFn:
    """HuggingFace tokenizer → 通用 encode_fn（prompt 段 labels 置 -100）。"""

    def encode(prompt: str, action: str):
        if torch is None:
            raise ImportError("make_hf_encode_fn 需要 torch")
        p = tokenizer(prompt, return_tensors="pt")
        a = tokenizer(action, add_special_tokens=False, return_tensors="pt")
        input_ids = torch.cat([p["input_ids"], a["input_ids"]], dim=1)
        labels = torch.full_like(input_ids, -100)
        labels[:, p["input_ids"].shape[1]:] = a["input_ids"]
        attn = torch.ones_like(input_ids)
        return (input_ids.to(device), labels.to(device), int(p["input_ids"].shape[1]), attn.to(device))

    return encode


class OfflineLearner:
    def __init__(self, model, value_head, encode_fn: EncodeFn,
                 config: Optional[TrainConfig] = None, device: str = "cpu") -> None:
        if torch is None:
            raise ImportError("OfflineLearner 需要 torch")
        self.model = model
        self.value_head = value_head
        self.encode_fn = encode_fn
        self.cfg = config or TrainConfig()
        self.device = device
        policy_params = [p for p in model.parameters() if p.requires_grad]
        self.optimizer = torch.optim.AdamW(
            [
                {"params": policy_params, "lr": self.cfg.policy_lr},
                {"params": list(value_head.parameters()), "lr": self.cfg.value_lr},
            ]
        )
        self.optimizer_steps = 0

    # ------------------------------------------------------------------ #
    def update(self, batch: Sequence[Transition],
               weights: Optional[Sequence[float]] = None) -> Dict[str, Any]:
        """一次专家迭代更新（一个 optimizer step；micro 分块梯度累积）。

        超时样本默认剔除（官方：timeout 不进训练）；反证样本按 include_disproof 控制。
        """
        pairs = []
        skipped: Dict[str, int] = {}
        for i, t in enumerate(batch):
            if t.kind == "timeout" and not self.cfg.include_timeout:
                skipped["timeout"] = skipped.get("timeout", 0) + 1
                continue
            if t.kind == "disproof" and not self.cfg.include_disproof:
                skipped["disproof"] = skipped.get("disproof", 0) + 1
                continue
            w = (weights[i] if weights is not None and i < len(weights)
                 else (self.cfg.disproof_weight if t.kind == "disproof" else 1.0))
            pairs.append((t, float(w)))
        if not pairs:
            return {"skipped": True, "reason": "empty batch", "kind_skipped": skipped}
        batch = [t for t, _ in pairs]
        weights = [w for _, w in pairs]
        kind_counts: Dict[str, int] = {}
        for t in batch:
            kind_counts[t.kind] = kind_counts.get(t.kind, 0) + 1

        self.model.train()
        self.value_head.train()
        self.optimizer.zero_grad(set_to_none=True)

        policy_total = torch.zeros((), device=self.device)
        value_total = torch.zeros((), device=self.device)
        weight_sum = 0.0
        value_n = 0
        micro = max(1, int(self.cfg.micro_batch_size))
        for start in range(0, len(batch), micro):
            chunk = batch[start:start + micro]
            chunk_w = weights[start:start + micro]
            for sample, w in zip(chunk, chunk_w):
                encoded = self.encode_fn(sample.prompt, sample.action)
                input_ids, labels, prompt_len = encoded[0], encoded[1], encoded[2]
                attn = encoded[3] if len(encoded) > 3 else None
                out = self.model(input_ids=input_ids, attention_mask=attn, labels=labels,
                                 output_hidden_states=True, use_cache=False, return_dict=True)
                policy_sample = self._policy_ce(out.logits, labels)
                policy_total = policy_total + float(w) * policy_sample
                weight_sum += float(w)
                if sample.value_target is not None:
                    hidden = out.hidden_states[-1][:, prompt_len - 1, :].float()
                    pred = self.value_head(hidden).reshape(1, -1)
                    target = self._two_hot(-float(sample.value_target)).to(pred.device)
                    value_total = value_total + F.cross_entropy(pred, target)
                    value_n += 1

        policy_mean = policy_total / max(weight_sum, 1e-9)
        value_mean = value_total / max(value_n, 1)
        total = policy_mean + self.cfg.value_coef * value_mean
        if not bool(torch.isfinite(total).item()):
            self.optimizer.zero_grad(set_to_none=True)
            raise FloatingPointError("non-finite offline loss")
        total.backward()
        params = [p for group in self.optimizer.param_groups for p in group["params"] if p.requires_grad]
        grad_norm = torch.nn.utils.clip_grad_norm_(params, self.cfg.max_grad_norm)
        self.optimizer.step()
        self.optimizer_steps += 1
        self.model.eval()
        self.value_head.eval()
        return {
            "loss": float(total.detach().cpu()),
            "policy_loss": float(policy_mean.detach().cpu()),
            "value_loss": float(value_mean.detach().cpu()),
            "grad_norm": float(torch.as_tensor(grad_norm).detach().cpu()),
            "samples": len(batch),
            "value_samples": value_n,
            "kind_counts": kind_counts,
            "optimizer_steps": self.optimizer_steps,
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _policy_ce(logits, labels):
        """单样本 CE：对 action 段所有 token 求平均（prompt 段 -100 已掩码）。"""
        shift_logits = logits[:, :-1, :]
        shift_labels = labels[:, 1:]
        vocab = shift_logits.size(-1)
        loss = F.cross_entropy(shift_logits.reshape(-1, vocab), shift_labels.reshape(-1),
                               ignore_index=-100, reduction="none")
        mask = (shift_labels != -100).reshape(-1)
        n = mask.sum().clamp(min=1)
        return (loss * mask).sum() / n

    @staticmethod
    def _two_hot(distance: float):
        from alphaproof.net.value_head import two_hot_target
        return two_hot_target(distance, bins=64)

"""本机式在线更新 learner（RTTT / TTTRL）。

与原实现的差异（对齐修正）：
- value target 用剩余步数 d(s)（64 桶 CE），**不再用标量 r 兜底**（修 G1）；
- 一个奖励仍同时驱动 policy 与 value 两组参数；
- `learn_batch_size` 可配：=1 表示一条已验证轨迹更新一次；=N 表示凑够 N 条更新一次；
- 正奖励必须 terminal_verified=true；
- 更新前快照、非有限损失自动回滚。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from alphaproof.config import TrainConfig
from alphaproof.data.events import Transition

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]

EncodeFn = Callable[..., Any]


def make_hf_encode_fn(tokenizer, device: str = "cpu"):
    """与离线包等价的 HF encode_fn。"""
    from update_offline.learner import make_hf_encode_fn as _mk
    return _mk(tokenizer, device)


class OnlineLearner:
    def __init__(self, model, value_head, encode_fn: EncodeFn,
                 config: Optional[TrainConfig] = None, device: str = "cpu") -> None:
        if torch is None:
            raise ImportError("OnlineLearner 需要 torch")
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
        self.queue: List[Transition] = []
        self.update_index = 0
        self.optimizer_steps = 0
        self.last_receipt: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ #
    def submit(self, event: Transition) -> Optional[Dict[str, Any]]:
        """入队一条已验证轨迹；达到 learn_batch_size 时执行一次更新。

        返回：本次触发更新的回执；未触发则返回 None。
        """
        reward = event.reward if event.reward is not None else 0.0
        reward = float(reward)
        if not torch.is_tensor(reward) and not (-1.0 <= reward <= 1.0):
            raise ValueError("reward must be in [-1, 1]")
        if reward > 0 and not event.terminal_verified:
            raise ValueError("positive reward requires terminal_verified=true")
        if event.logprob_old is None:
            raise ValueError("online 事件必须携带 logprob_old（采样时的旧策略对数概率）")
        self.queue.append(event)
        if len(self.queue) >= max(1, int(self.cfg.learn_batch_size)):
            return self._update_and_clear()
        return None

    def flush(self) -> Optional[Dict[str, Any]]:
        if self.queue:
            return self._update_and_clear()
        return None

    # ------------------------------------------------------------------ #
    def _update_and_clear(self) -> Dict[str, Any]:
        events = list(self.queue)
        self.queue.clear()
        receipt = self.update(events)
        self.update_index += 1
        self.last_receipt = receipt
        return receipt

    def update(self, events: List[Transition]) -> Dict[str, Any]:
        """一次更新：-r·Δ + β·Δ²（policy）+ value_coef · CE64（value）。"""
        snapshot = self._snapshot()
        self.model.train()
        self.value_head.train()
        self.optimizer.zero_grad(set_to_none=True)
        try:
            policy_total = torch.zeros((), device=self.device)
            value_total = torch.zeros((), device=self.device)
            value_n = 0
            for event in events:
                encoded = self.encode_fn(event.prompt, event.action)
                input_ids, labels, prompt_len = encoded[0], encoded[1], encoded[2]
                attn = encoded[3] if len(encoded) > 3 else None
                out = self.model(input_ids=input_ids, attention_mask=attn, labels=labels,
                                 output_hidden_states=True, use_cache=False, return_dict=True)
                logp = -self._token_nll_sum(out.logits, labels)
                delta = logp - float(event.logprob_old)
                reward = float(event.reward or 0.0)
                policy_total = policy_total + (-reward * delta + self.cfg.beta_kl * delta.pow(2))
                if event.value_target is not None:
                    hidden = out.hidden_states[-1][:, prompt_len - 1, :].float()
                    pred = self.value_head(hidden).reshape(1, -1)
                    target = self._two_hot(-float(event.value_target)).to(pred.device)
                    value_total = value_total + F.cross_entropy(pred, target)
                    value_n += 1
            n = max(len(events), 1)
            policy_mean = policy_total / n
            value_mean = value_total / max(value_n, 1)
            total = policy_mean + self.cfg.value_coef * value_mean
            if not bool(torch.isfinite(total).item()):
                raise FloatingPointError("non-finite online loss")
            total.backward()
            params = [p for group in self.optimizer.param_groups for p in group["params"]
                      if p.requires_grad]
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
                "samples": len(events),
                "value_samples": value_n,
                "update_index": self.update_index,
                "optimizer_steps": self.optimizer_steps,
            }
        except Exception:
            self._restore(snapshot)
            self.model.eval()
            self.value_head.eval()
            raise

    # ------------------------------------------------------------------ #
    @staticmethod
    def _token_nll_sum(logits, labels):
        """action 段的 NLL 之和（对应原实现 logp = -NLL）。"""
        shift_logits = logits[:, :-1, :]
        shift_labels = labels[:, 1:]
        vocab = shift_logits.size(-1)
        loss = F.cross_entropy(shift_logits.reshape(-1, vocab), shift_labels.reshape(-1),
                               ignore_index=-100, reduction="none")
        mask = (shift_labels != -100).reshape(-1)
        return (loss * mask).sum()

    @staticmethod
    def _two_hot(distance: float):
        from alphaproof.net.value_head import two_hot_target
        return two_hot_target(distance, bins=64)

    def _snapshot(self):
        return {
            "model": {k: v.detach().clone() for k, v in self.model.state_dict().items()},
            "value": {k: v.detach().clone() for k, v in self.value_head.state_dict().items()},
        }

    def _restore(self, snapshot) -> None:
        self.model.load_state_dict(snapshot["model"])
        self.value_head.load_state_dict(snapshot["value"])

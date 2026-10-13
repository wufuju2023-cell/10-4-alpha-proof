"""无依赖的小模型/分词器（需要 torch），用于冒烟与单测。

形状/接口与 HF CausalLM 对齐（logits、hidden_states、loss、labels=-100 掩码），
便于在无 7B、无 Lean 的机器上验证两种更新回路。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]


class TinyTokenizer:
    """字符级分词器：足够让 encode/labels 流程跑通。"""

    def __init__(self, vocab_size: int = 128) -> None:
        self.vocab_size = vocab_size

    def __len__(self) -> int:
        return self.vocab_size

    def encode(self, text: str) -> List[int]:
        return [ord(c) % self.vocab_size for c in text] or [0]

    def __call__(self, text: str, return_tensors: str = "pt", add_special_tokens: bool = True) -> Dict[str, "torch.Tensor"]:
        if torch is None:
            raise ImportError("TinyTokenizer 需要 torch")
        ids = self.encode(text)
        return {"input_ids": torch.tensor([ids], dtype=torch.long)}


if torch is not None:  # pragma: no cover

    class TinyCausalLM(nn.Module):  # type: ignore[misc]
        def __init__(self, vocab_size: int = 128, hidden_size: int = 64) -> None:
            super().__init__()
            self.vocab_size = vocab_size
            self.hidden_size = hidden_size
            self.emb = nn.Embedding(vocab_size, hidden_size)
            self.mix = nn.Linear(hidden_size, hidden_size)
            self.lm_head = nn.Linear(hidden_size, vocab_size)
            self.config = SimpleNamespace(hidden_size=hidden_size, use_cache=False)
            self._last_loss = None

        def forward(self, input_ids, attention_mask=None, labels=None,
                    output_hidden_states: bool = False, use_cache: bool = False,
                    return_dict: bool = True, **kwargs):
            h = torch.tanh(self.mix(self.emb(input_ids)))
            logits = self.lm_head(h)
            loss = None
            if labels is not None:
                loss = F.cross_entropy(
                    logits[:, :-1].reshape(-1, self.vocab_size),
                    labels[:, 1:].reshape(-1), ignore_index=-100,
                )
            return SimpleNamespace(
                logits=logits,
                hidden_states=(h,) if output_hidden_states else None,
                loss=loss,
            )


def build_tiny() -> tuple:
    """(model, tokenizer) 便捷构造。"""
    if torch is None:
        raise ImportError("build_tiny 需要 torch")
    return TinyCausalLM(), TinyTokenizer()


def tiny_encode_fn(tokenizer: TinyTokenizer, device: str = "cpu"):
    """与 update_*.learner.make_hf_encode_fn 同构的 encode_fn。"""

    def encode(prompt: str, action: str):
        p = tokenizer(prompt)
        a = tokenizer(action, add_special_tokens=False)
        input_ids = torch.cat([p["input_ids"], a["input_ids"]], dim=1)
        labels = torch.full_like(input_ids, -100)
        labels[:, p["input_ids"].shape[1]:] = a["input_ids"]
        attn = torch.ones_like(input_ids)
        return input_ids.to(device), labels.to(device), int(p["input_ids"].shape[1]), attn.to(device)

    return encode

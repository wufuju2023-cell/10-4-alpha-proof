"""策略+价值双头前向（可选组件，需要 torch/transformers）。

与 N25 的“一次 forward 两路输出”一致：
- policy：backbone 的 LM head logits [B, T, V]；
- value：最后一个有效 token 的 hidden [B, H] → 64 桶 logits [B, 64]（fp32）。
"""

from __future__ import annotations

from typing import Optional

try:
    import torch
    import torch.nn as nn
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


def last_token_hidden(hidden_states, attention_mask):
    """padding-aware 的“最后有效 token” hidden。"""
    if torch is None:
        raise ImportError("last_token_hidden 需要 torch")
    last_idx = attention_mask.long().sum(dim=1) - 1
    batch = torch.arange(hidden_states.size(0), device=hidden_states.device)
    return hidden_states[batch, last_idx]


if torch is not None:  # pragma: no cover

    class PolicyValueModel(nn.Module):  # type: ignore[misc]
        def __init__(self, backbone, value_head) -> None:
            super().__init__()
            self.backbone = backbone
            self.value_head = value_head

        def forward(self, input_ids, attention_mask=None):
            out = self.backbone(
                input_ids=input_ids,
                attention_mask=attention_mask,
                output_hidden_states=True,
                use_cache=False,
                return_dict=True,
            )
            hidden = last_token_hidden(out.hidden_states[-1].float(), attention_mask)
            return out.logits, self.value_head(hidden)

else:  # pragma: no cover

    class PolicyValueModel:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("PolicyValueModel 需要安装 torch")

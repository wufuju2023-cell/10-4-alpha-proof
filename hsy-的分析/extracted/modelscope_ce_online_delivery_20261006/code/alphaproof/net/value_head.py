"""64 桶价值头（唯一形态：官方 Table 2 categorical，无 tanh 标量版）。

- 结构：Linear(H→256) → SiLU → Linear(256→64)，H=3584（REAL-Prover 7B）；
- 目标：剩余步数 d(s) 的两热（two-hot）分布，d 截断到 [1,64]；
- 解码：d̂ = Σ p_b · b（b=1..64），搜索用 V = -d̂。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

try:  # torch 为可选依赖：纯 CPU 端（无 torch）也要能 import 本模块做形状/配置检查
    import torch
    import torch.nn as nn
except Exception:  # pragma: no cover - 无 torch 环境
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


def distance_to_bin_index(distance: float, bins: int = 64) -> int:
    """剩余步数 → 0-based 桶下标；d 截断到 [1, bins]（终端 0 → 桶 1）。"""
    d = min(max(float(distance), 1.0), float(bins))
    return int(math.floor(d)) - 1


def expected_distance(logits, bins: int = 64):
    """logits [B, bins] → 期望剩余步数 d̂（返回 torch.Tensor）。"""
    if torch is None:
        raise ImportError("expected_distance 需要 torch")
    p = torch.softmax(logits.float(), dim=-1)
    idx = torch.arange(1, bins + 1, device=p.device, dtype=torch.float32)
    return (p * idx).sum(dim=-1)


def two_hot_target(distance, bins: int = 64, device=None):
    """剩余步数（标量或 tensor）→ 两热目标 [B, bins]。"""
    if torch is None:
        raise ImportError("two_hot_target 需要 torch")
    if not torch.is_tensor(distance):
        distance = torch.tensor([float(distance)], dtype=torch.float32)
    d = distance.float().clamp(min=1.0, max=float(bins))
    lower = d.floor().clamp(min=1.0, max=float(bins))
    upper = (lower + 1.0).clamp(max=float(bins))
    frac = (d - lower).clamp(min=0.0, max=1.0)
    target = torch.zeros((d.shape[0], bins), dtype=torch.float32, device=distance.device)
    lower_idx = (lower - 1.0).long()
    upper_idx = (upper - 1.0).long()
    target.scatter_add_(1, lower_idx.unsqueeze(1), (1.0 - frac).unsqueeze(1))
    target.scatter_add_(1, upper_idx.unsqueeze(1), frac.unsqueeze(1))
    if device is not None:
        target = target.to(device)
    return target


if torch is not None:  # pragma: no cover - 需要 torch

    class ValueHead64(nn.Module):  # type: ignore[misc]
        """64 桶分类价值头（fp32）。"""

        def __init__(self, hidden_size: int = 3584, mid: int = 256, bins: int = 64) -> None:
            super().__init__()
            self.hidden_size = hidden_size
            self.bins = bins
            self.net = nn.Sequential(
                nn.Linear(hidden_size, mid),
                nn.SiLU(),
                nn.Linear(mid, bins),
            )

        def forward(self, hidden):  # [B, H] -> [B, bins]
            return self.net(hidden.float())

        def decode(self, logits):
            return expected_distance(logits, self.bins)

        @classmethod
        def from_backbone(cls, backbone, hidden_size: Optional[int] = None,
                          mid: int = 256, bins: int = 64) -> "ValueHead64":
            hidden = hidden_size or int(backbone.config.hidden_size)
            return cls(hidden, mid, bins)

    def load_s18_head(head: "ValueHead64", path: str, map_location: str = "cpu") -> dict:
        """加载云端 64 桶头（兼容裸 state_dict / {'state_dict'|'model'|'value_head': ...} 包装 /
        '0.weight' 无前缀键名）。返回 missing/unexpected 列表。"""
        obj = torch.load(path, map_location=map_location)
        state = obj
        if isinstance(state, dict):
            for wrapper in ("state_dict", "model", "value_head"):
                inner = state.get(wrapper)
                if isinstance(inner, dict) and inner and hasattr(next(iter(inner.values())), "shape"):
                    state = inner
                    break
        model_keys = set(head.state_dict().keys())
        if isinstance(state, dict) and not model_keys.issubset(set(state.keys())):
            remapped = {}
            for key, value in state.items():
                if key in model_keys:
                    remapped[key] = value
                elif ("net." + key) in model_keys:
                    remapped["net." + key] = value
                else:
                    remapped[key] = value
            state = remapped
        missing, unexpected = head.load_state_dict(state, strict=False)
        return {"missing": list(missing), "unexpected": list(unexpected)}

else:  # pragma: no cover

    class ValueHead64:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("ValueHead64 需要安装 torch")

    def load_s18_head(*args, **kwargs):
        raise ImportError("load_s18_head 需要安装 torch")

"""训练事件与轨迹的数据结构（CPU↔GPU 过河用的最小 schema）。

语义约定（对齐官方）：
- value_target = -d(s)，d(s) 是“剩余步数”；终端（证明/反证完成）为 0；
- OR 节点 value_target = -1 + 子节点；AND 节点取 min（最坏分支）；
- kind: proof（证明）/ disproof（反证）/ timeout（超时，默认不进训练）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Transition:
    """一次 (state, action) 训练样本。"""

    prompt: str                     # 状态（Lean tactic state）文本
    action: str                     # 战术字符串
    value_target: Optional[float] = None   # -d(s)；官方口径的剩余步数标签
    kind: str = "proof"             # proof | disproof | timeout
    solved: bool = False            # 该轨迹是否最终闭合（内核验证）
    terminal_verified: bool = False  # 正奖励/证明必须由独立验证器确认
    logprob_old: Optional[float] = None    # 采样时旧策略的 log 概率（在线模式）
    reward: Optional[float] = None         # 标量奖励（在线模式；与 value_target 分离）
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Transition":
        known = {k: v for k, v in d.items() if k in Transition.__dataclass_fields__}
        return Transition(**known)

    @property
    def distance(self) -> Optional[float]:
        """剩余步数 d = -value_target（与 value 符号相反）。"""
        if self.value_target is None:
            return None
        return -float(self.value_target)


@dataclass
class Trajectory:
    """一次搜索会话的成功/失败轨迹。"""

    problem_id: str
    solved: bool
    transitions: list[Transition] = field(default_factory=list)
    kind: str = "proof"             # proof | disproof | timeout
    meta: dict[str, Any] = field(default_factory=dict)

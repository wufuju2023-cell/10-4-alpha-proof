"""课程机制的数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 一次尝试的结局（unknown = 提交结果未知，必须人工对账，不当作数学失败）
OUTCOMES = ("solved", "exhausted", "disproved", "timeout", "unknown")


@dataclass(frozen=True)
class Lesson:
    """一课：一条待证明命题（或待反证命题的父母）。"""

    lesson_id: str
    name: str
    statement_ref: str                 # 指向题面（文件#符号）
    depends_on: Tuple[str, ...] = ()   # 依赖的课（先掌握依赖再解锁）
    tags: Tuple[str, ...] = ()

    @staticmethod
    def from_dict(d: dict) -> "Lesson":
        return Lesson(
            lesson_id=str(d["lesson_id"]),
            name=str(d["name"]),
            statement_ref=str(d["statement_ref"]),
            depends_on=tuple(d.get("depends_on", ())),
            tags=tuple(d.get("tags", ())),
        )


@dataclass
class LessonState:
    """一课的调度状态（可持久化）。"""

    lesson_id: str
    history: List[str] = field(default_factory=list)   # 最近窗口的 outcome 序列
    attempts: int = 0
    consecutive_success: int = 0
    trusted: bool = False        # 连续成功 ≥ trust_count
    mastered: bool = False       # 连续成功 ≥ mastery_count（一致成功）
    disproved: bool = False      # 反证成功：永久排除
    blocked_unknown: bool = False  # 出现未知结果，禁止再调度

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LessonState":
        return LessonState(
            lesson_id=str(d["lesson_id"]),
            history=list(d.get("history", [])),
            attempts=int(d.get("attempts", 0)),
            consecutive_success=int(d.get("consecutive_success", 0)),
            trusted=bool(d.get("trusted", False)),
            mastered=bool(d.get("mastered", False)),
            disproved=bool(d.get("disproved", False)),
            blocked_unknown=bool(d.get("blocked_unknown", False)),
        )


@dataclass(frozen=True)
class CurriculumConfig:
    """课程/调度超参（默认值 = 官方 Table 7 与本机推荐的乘积）。"""

    name: str = "pell"
    budget_base: int = 250          # 官方 Table 7
    budget_multiplier: float = 1.17  # 官方 Table 7
    budget_cap: int = 16000         # 官方 Table 7
    history_window: int = 25        # v1 规格的 recent window
    trust_count: int = 8            # 官方 Table 7
    mastery_count: int = 12         # 官方 Table 7（trust_count_proved = 12）
    disprove_rate: float = 0.5      # 官方 Table 7
    # --- 三闸门（课程变体准入；本机 v1 规格）---
    difficulty_lo: float = 0.5      # 1 - solve@16 的下界
    difficulty_hi: float = 0.9      # 1 - solve@16 的上界
    structure_min: float = 0.7      # 结构相似度阈值
    # --- 推进门槛 ---
    advance_streak: int = 1         # 本机课程语义：连续成功 N 次即可解锁/推进（默认 1）
    strict_mastery: bool = False    # True 时采用官方严格口径：掌握（mastery_count 次）才推进
    seed: str = "pell-course-v1"    # 调度/极性用的确定性盐

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AttemptPlan:
    lesson_id: str
    budget: int
    polarity: str                   # prove | disprove


@dataclass
class AttemptResult:
    lesson_id: str
    outcome: str
    nodes: int = 0
    wall_ms: int = 0
    artifact: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

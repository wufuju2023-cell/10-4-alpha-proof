"""课程变体准入的三闸门（本机 v1 规格；比较函数由调用方注入）。

G1 编译：变体在 Lean 中可编译且类型正确（外部 checker 结果）；
G2 难度：1 - solve@16 ∈ [lo, hi]（既不能太难也不能太简单）；
G3 结构：与原目标的结构相似度 Sim ≥ 阈值。

本模块只做判定；不实现形式化生成（teacher/autoformalization 仍属未实现）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from .models import CurriculumConfig


@dataclass(frozen=True)
class GateReport:
    compile_ok: bool
    difficulty_ok: bool
    structure_ok: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def admitted(self) -> bool:
        return self.compile_ok and self.difficulty_ok and self.structure_ok

    def to_dict(self) -> dict:
        return {
            "compile_ok": self.compile_ok,
            "difficulty_ok": self.difficulty_ok,
            "structure_ok": self.structure_ok,
            "admitted": self.admitted,
            "reasons": list(self.reasons),
        }


def evaluate_gates(
    *,
    compiles: bool,
    solve_rate_at_16: float,
    similarity: float,
    config: CurriculumConfig,
) -> GateReport:
    """三闸门判定（参数范围均有显式校验）。"""
    if not 0.0 <= solve_rate_at_16 <= 1.0:
        raise ValueError("solve_rate_at_16 must be in [0, 1]")
    if similarity < 0.0:
        raise ValueError("similarity must be >= 0")

    difficulty = 1.0 - solve_rate_at_16
    difficulty_ok = config.difficulty_lo <= difficulty <= config.difficulty_hi
    structure_ok = similarity >= config.structure_min

    reasons = []
    if not compiles:
        reasons.append("G1: 变体未通过 Lean 编译/类型检查")
    if not difficulty_ok:
        reasons.append(
            f"G2: 难度 {difficulty:.3f} 不在 [{config.difficulty_lo}, {config.difficulty_hi}]"
        )
    if not structure_ok:
        reasons.append(f"G3: 结构相似度 {similarity:.3f} < {config.structure_min}")
    if not reasons:
        reasons.append("admitted")
    return GateReport(compiles, difficulty_ok, structure_ok, tuple(reasons))


def solve_rate_from_results(solved: int, total: int) -> float:
    """由 solve@16 实测（若干次固定预算尝试）得到 solve_rate。"""
    if total <= 0:
        raise ValueError("total must be > 0")
    if not 0 <= solved <= total:
        raise ValueError("solved must be in [0, total]")
    return solved / total

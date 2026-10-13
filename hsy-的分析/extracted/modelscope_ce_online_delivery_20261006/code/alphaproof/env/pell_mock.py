"""佩尔题（Pell）冒烟用的确定性 Mock 环境。

背景：本题取自用户已验收实验 `20260828-real7b-pell-success` 中的原题
（∀ B, ∃ x y, B < x ∧ B < y ∧ x² = 2y² + 1）。本 Mock **不是 Lean 内核**，
只是为了在无 Lean 工具链的机器上验证“搜索 → 轨迹 → 两种更新”的数据流：
- 战术表按真实证明骨架（intro → refine ⟨17,12,?_,?_⟩ → 两支 norm_num）写死；
- 第 2 步产生 2 个子目标 → AND 节点 + focus 子节点（覆盖 AND 逻辑）；
- 策略/价值是查表函数，含无效战术噪声（sorry / aesop）以覆盖过滤路径。

接真实 Lean 的路线见 docs/real_lean_next.md。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .base import TacticResult

PELL_TARGET_STATEMENT = (
    "def Target : Prop :=\n"
    "  ∀ B : ℕ, ∃ x y : ℕ, B < x ∧ B < y ∧ x ^ 2 = 2 * y ^ 2 + 1"
)

# --- 状态句柄与展示文本（ASCII，避免终端编码问题）---
ROOT = "pell.root"
STATE_B = "pell.B"
STATE_AND = "pell.AND"
STATE_EQ = "pell.eq"
STATE_BOUNDS = "pell.bounds"
STATE_CLOSED = "pell.closed"

REPRS: Dict[str, str] = {
    ROOT: "|- CodexMathFive.Pell.Target",
    STATE_B: "B : Nat |- exists x y : Nat, B < x /\\ B < y /\\ x^2 = 2*y^2 + 1",
    STATE_AND: "|- (17^2 = 2*12^2 + 1) /\\ (B < 17 /\\ B < 12)",
    STATE_EQ: "|- 17^2 = 2*12^2 + 1",
    STATE_BOUNDS: "|- B < 17 /\\ B < 12",
    STATE_CLOSED: "no goals",
}

TRANSITIONS: Dict[str, Dict[str, Tuple[str, int, List[str]]]] = {
    ROOT: {"intro B": (STATE_B, 1, [])},
    STATE_B: {"refine <17, 12, ?_, ?_>": (STATE_AND, 2, [STATE_EQ, STATE_BOUNDS])},
    STATE_EQ: {"norm_num": (STATE_CLOSED, 0, [])},
    STATE_BOUNDS: {"norm_num": (STATE_CLOSED, 0, [])},
}

# 真实成功脚本（以 Mock 战术文本表示）——check_proof 用它做“内核通过”的替身
EXPECTED_PELL_SCRIPT = [
    "intro B",
    "refine <17, 12, ?_, ?_>",
    "norm_num",
    "norm_num",
]

# --- Mock 策略/价值（查表）---
_POLICY: Dict[str, List[Tuple[str, float]]] = {
    REPRS[ROOT]: [("intro B", -0.10), ("sorry", -1.50), ("exact pell_target", -2.00)],
    REPRS[STATE_B]: [("refine <17, 12, ?_, ?_>", -0.20), ("aesop", -1.20)],
    REPRS[STATE_EQ]: [("norm_num", -0.05), ("ring", -2.00)],
    REPRS[STATE_BOUNDS]: [("norm_num", -0.05), ("omega", -1.80)],
}

_VALUES: Dict[str, float] = {
    REPRS[ROOT]: -4.0,        # d ≈ 4
    REPRS[STATE_B]: -3.0,
    REPRS[STATE_EQ]: -1.0,
    REPRS[STATE_BOUNDS]: -1.0,
}


class PellMockEnv:
    """确定性环境：战术表 + 禁用词 + 脚本复核。"""

    def __init__(self, allow_only_expected: bool = True) -> None:
        self.allow_only_expected = allow_only_expected

    def pp_state(self, state: str) -> str:
        return REPRS.get(state, state)

    def apply(self, state: str, tactic: str) -> TacticResult:
        tactic = tactic.strip()
        if tactic in {"sorry", "admit"}:
            return TacticResult(ok=False, error="forbidden tactic")
        table = TRANSITIONS.get(state, {})
        if tactic not in table:
            return TacticResult(ok=False, error=f"not applicable: {tactic}")
        nxt, num_goals, subs = table[tactic]
        return TacticResult(
            ok=True,
            next_state=nxt,
            num_goals=num_goals,
            subgoal_states=list(subs),
            closed=(num_goals == 0),
        )

    def check_proof(self, state: str, script: List[str]) -> bool:
        if self.allow_only_expected:
            return [s.strip() for s in script] == EXPECTED_PELL_SCRIPT
        return False


def mock_policy(state_repr: str) -> List[Tuple[str, float]]:
    return list(_POLICY.get(state_repr, []))


def mock_value(state_repr: str) -> float:
    return _VALUES.get(state_repr, 0.0)

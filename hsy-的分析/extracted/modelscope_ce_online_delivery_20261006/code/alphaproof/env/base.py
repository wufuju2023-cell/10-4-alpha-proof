"""Lean 环境的最小协议（对齐 N28 的 LeanEnv 三原语）。

真实实现（Lean 子进程 / LSP）由 v1 runtime 提供；本仓库的测试与冒烟使用
`pell_mock.PellMockEnv` 或后续接入的真实 Bridge。搜索层只依赖本协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class TacticResult:
    ok: bool
    next_state: Optional[str] = None
    num_goals: int = 0
    subgoal_states: List[str] = field(default_factory=list)
    closed: bool = False
    error: str = ""


class LeanEnv(Protocol):
    def pp_state(self, state: str) -> str:
        """状态句柄 → 可读的 goal 文本（进 prompt / 树日志）。"""
        ...

    def apply(self, state: str, tactic: str) -> TacticResult:
        """在状态上执行一条 tactic，返回新状态与子目标数。"""
        ...

    def check_proof(self, state: str, script: List[str]) -> bool:
        """独立复验：重放脚本并做内核检查（Mock 里为脚本比对）。"""
        ...

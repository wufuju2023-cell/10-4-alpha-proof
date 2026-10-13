"""端到端流水线（Mock 版）：搜索 → 轨迹 → 训练事件。

真实 Lean/GPU 版本沿用相同的接口：搜索在 CPU（Lean），学习在 GPU；
本模块先用 Pell Mock 环境把数据流跑通。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .config import SearchConfig
from .data.events import Trajectory, Transition
from .env.pell_mock import PellMockEnv, mock_policy, mock_value
from .mcts.search import MCTS, SearchResult
from .targets.value_targets import extract_transitions


def run_mock_search(config: Optional[SearchConfig] = None) -> SearchResult:
    env = PellMockEnv()
    mcts = MCTS(env=env, policy=mock_policy, value=mock_value, config=config or SearchConfig())
    return mcts.search("pell.root")


def result_to_trajectory(result: SearchResult, problem_id: str = "pell-target",
                         verify: bool = True) -> Trajectory:
    """把已解出的搜索树转成成功轨迹（含官方 value target 回溯）。"""
    env = PellMockEnv()
    transitions: List[Transition] = []
    for prompt, action, value_target in extract_transitions(result.root):
        transitions.append(
            Transition(
                prompt=prompt,
                action=action,
                value_target=value_target,
                kind="proof",
                solved=True,
                terminal_verified=verify and env.check_proof("pell.root", result.best_script),
            )
        )
    return Trajectory(problem_id=problem_id, solved=result.solved, transitions=transitions,
                      kind="proof", meta=result.summary())


def make_failure_samples() -> List[Trajectory]:
    """冒烟用的反证/超时样本（官方：反证进训练，超时不进）。"""
    disproof = Trajectory(
        problem_id="pell-variant-disproof",
        solved=True,
        kind="disproof",
        transitions=[Transition(prompt="|- False", action="exact pell_no_solution",
                                value_target=0.0, kind="disproof", solved=True,
                                terminal_verified=True)],
    )
    timeout = Trajectory(
        problem_id="pell-variant-timeout",
        solved=False,
        kind="timeout",
        transitions=[Transition(prompt="|- hard goal", action="aesop",
                                value_target=None, kind="timeout", solved=False)],
    )
    return [disproof, timeout]


def save_transitions(path: str | Path, transitions: List[Transition]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for t in transitions:
            f.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")


def load_transitions(path: str | Path) -> List[Transition]:
    out: List[Transition] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Transition.from_dict(json.loads(line)))
    return out

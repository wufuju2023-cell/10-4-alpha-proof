#!/usr/bin/env python3
"""E3 mock 三臂 harness（纯 CPU，0 GPU，0 Lean）。

目的：把 E3「搜索 vs 采样、同预算对照」的实验骨架先跑通（G3 前置件）。
真实验（真 policy/value + 真 Lean、固定题集与预算）在 GPU 恢复后按
`docs/diversity-mcts/7-GPU待办清单.md` 的 G3 项执行。

三臂（预算 = env.apply 次数，统一封顶）：
  (a) sampling    ：逐步均匀采样；失败即重采、成功前进（best-of-B 的串行版）
  (b) mcts_value  ：本机对齐 PUCT MCTS + 完美价值 V = -剩余步数
  (c) mcts_novalue：同一 MCTS，value=None（纯先验 + 环境反馈）

环境：链式 mock。每级 4 个候选：
  - step  ：推进一级（正确）；
  - side  ：推进一级但把总需求 +2（"弯路"，价值函数理论上可识别）；
  - bad_a / bad_b：直接失败。
先验平（logprob=0）→ (b)/(c) 的差异只来自价值备份，(a) 是纯采样基线。

重要：mock 数字只用于验证"预算口径 / 三臂接线 / 指标落盘"，**不是实验证据**。

用法：
    cd /mnt/gloway/projects/10-4-alpha-proof
    PYTHONPATH=. ~/venvs/alpha104/bin/python scripts/e3_mock_three_arms.py \
        --instances 300 --depth 4 --budget 32 \
        --out docs/diversity-mcts/reports/e3-mock-arms.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from random import Random

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaproof.config import SearchConfig          # noqa: E402
from alphaproof.env.base import TacticResult        # noqa: E402
from alphaproof.mcts.search import MCTS             # noqa: E402

TACTICS = ["step", "side", "bad_a", "bad_b"]
START = "chain/l0:0"


class ChainEnv:
    """确定性链式环境；预算按 apply 次数封顶（三臂统一口径）。"""

    def __init__(self, depth: int, budget: int) -> None:
        self.depth = depth
        self.budget = budget
        self.used = 0

    @staticmethod
    def _parse(key: str) -> tuple[int, int]:
        body = key.split("/", 1)[1]            # l{level}:{extra}
        lvl, extra = body[1:].split(":")
        return int(lvl), int(extra)

    def remaining(self, key: str) -> int:
        if key == "chain/closed":
            return 0
        lvl, extra = self._parse(key)
        return self.depth + extra - lvl

    def pp_state(self, key: str) -> str:
        return f"goal: remaining {self.remaining(key)}"

    def apply(self, key: str, tactic: str) -> TacticResult:
        if self.used >= self.budget:
            return TacticResult(ok=False, error="budget exhausted")
        self.used += 1
        if key == "chain/closed":
            return TacticResult(ok=False, error="already closed")
        lvl, extra = self._parse(key)
        if tactic == "step":
            nlvl, nextra = lvl + 1, extra
        elif tactic == "side":
            nlvl, nextra = lvl + 1, extra + 2
        else:
            return TacticResult(ok=False, error=f"not applicable: {tactic}")
        if nlvl >= self.depth + nextra:
            return TacticResult(ok=True, next_state="chain/closed", num_goals=0, closed=True)
        return TacticResult(ok=True, next_state=f"chain/l{nlvl}:{nextra}", num_goals=1)

    def check_proof(self, key, script):  # 仅骨架用
        return bool(script)


def flat_policy(_state_repr: str):
    """候选表平先验：学习信号只来自价值与环境反馈。"""
    return [(t, 0.0) for t in TACTICS]


def perfect_value(state_repr: str) -> float:
    """V = -剩余步数（完美价值；用于 arm b）。"""
    return -float(state_repr.rsplit(" ", 1)[1])


def run_sampling(depth: int, budget: int, rng: Random) -> tuple[bool, int]:
    env = ChainEnv(depth, budget)
    key = START
    while True:
        r = env.apply(key, rng.choice(TACTICS))
        if r.ok:
            if r.next_state == "chain/closed":
                return True, env.used
            key = r.next_state
        elif env.used >= env.budget:
            return False, env.used


def run_mcts(depth: int, budget: int, use_value: bool) -> tuple[bool, int]:
    env = ChainEnv(depth, budget)
    cfg = SearchConfig(max_nodes=256, max_steps=256)
    mcts = MCTS(env=env, policy=flat_policy,
                value=perfect_value if use_value else None,
                config=cfg)
    result = mcts.search(START)
    return result.solved, env.used


def summarize(results: list[tuple[bool, int]]) -> dict:
    solved = [u for ok, u in results if ok]
    used = [u for _, u in results]
    return {
        "instances": len(results),
        "solved": len(solved),
        "solve_rate": len(solved) / len(results) if results else 0.0,
        "mean_applies_solved": statistics.fmean(solved) if solved else None,
        "p50_applies_solved": statistics.median(solved) if solved else None,
        "p90_applies_solved": (sorted(solved)[int(0.9 * (len(solved) - 1))] if solved else None),
        "mean_applies_all": statistics.fmean(used) if used else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, default=300)
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--budget", type=int, default=32)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="docs/diversity-mcts/reports/e3-mock-arms.json")
    args = ap.parse_args()

    arms: dict[str, list[tuple[bool, int]]] = {"sampling": [], "mcts_value": [], "mcts_novalue": []}
    for i in range(args.instances):
        rng = Random(f"{args.seed}-{i}-sampling")
        arms["sampling"].append(run_sampling(args.depth, args.budget, rng))
        arms["mcts_value"].append(run_mcts(args.depth, args.budget, True))
        arms["mcts_novalue"].append(run_mcts(args.depth, args.budget, False))

    print(f"E3 mock 三臂 harness：instances={args.instances} depth={args.depth} budget={args.budget}")
    print("（mock 数字仅为管线验证，不是实验证据）")
    print()
    print(f"{'arm':>14} {'solve_rate':>10} {'mean_applies(solved)':>20} {'p50':>5} {'p90':>5} {'mean(applies_all)':>17}")
    summary = {name: summarize(res) for name, res in arms.items()}
    for name in ("sampling", "mcts_value", "mcts_novalue"):
        s = summary[name]
        print(f"{name:>14} {s['solve_rate']:>10.3f} "
              f"{(s['mean_applies_solved'] or float('nan')):>20.2f} "
              f"{(s['p50_applies_solved'] or float('nan')):>5} "
              f"{(s['p90_applies_solved'] or float('nan')):>5} "
              f"{(s['mean_applies_all'] or float('nan')):>17.2f}")

    out = {
        "note": "mock harness only (E3 plumbing); NOT experiment evidence",
        "config": {"instances": args.instances, "depth": args.depth,
                   "budget": args.budget, "seed": args.seed,
                   "tactics": TACTICS},
        "arms": summary,
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("saved:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

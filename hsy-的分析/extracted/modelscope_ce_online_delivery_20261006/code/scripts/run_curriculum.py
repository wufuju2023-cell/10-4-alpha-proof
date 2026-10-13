#!/usr/bin/env python3
"""课程机制演示/运行器。

    # 演示（模拟执行器；展示预算增长与课程推进）
    python3 scripts/run_curriculum.py --course curriculum/pell_course.json \
        --state outputs/curriculum_demo_state.json --max-steps 40 --mock fail-once

    # 真实执行占位（需 Lean 环境；见 lean/pell_smoke/README.md）
    python3 scripts/run_curriculum.py --executor lean
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from curriculum import (AttemptPlan, AttemptResult, CurriculumConfig,  # noqa: E402
                        CurriculumRunner, Lesson)


def mock_executor(mode: str, fail_state: dict):
    """模拟执行器：按 mode 产生确定性结果，用于演示调度机制。"""

    def executor(lesson: Lesson, plan: AttemptPlan) -> AttemptResult:
        key = lesson.lesson_id
        if mode == "always-solve":
            return AttemptResult(key, "solved", nodes=min(64, plan.budget // 8), wall_ms=1000)
        # fail-once：每课第一次 exhausted，第二次 solved
        seen = fail_state.setdefault(key, 0) + 1
        fail_state[key] = seen
        if seen == 1:
            return AttemptResult(key, "exhausted", nodes=plan.budget, wall_ms=2000)
        return AttemptResult(key, "solved", nodes=min(64, plan.budget // 8), wall_ms=1500)

    return executor


def lean_executor(lesson: Lesson, plan: AttemptPlan) -> AttemptResult:
    """真实执行占位：包装 batch_solver 的接口（当前未接线）。"""
    raise NotImplementedError(
        "真实执行请见 lean/pell_smoke/README.md："
        "用 batch_solver + 对齐内核运行对应 theorem 文件，"
        "并把 result.json 映射为 AttemptResult。"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--course", default="curriculum/pell_course.json")
    ap.add_argument("--state", default="outputs/curriculum_state.json")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--executor", choices=["mock", "lean"], default="mock")
    ap.add_argument("--mock", choices=["fail-once", "always-solve"], default="fail-once")
    ap.add_argument("--config", default="", help="可选：CurriculumConfig JSON 覆盖")
    args = ap.parse_args()

    base = Path(__file__).resolve().parents[1]
    lessons = CurriculumRunner.load_lessons(str(base / args.course))
    config = CurriculumConfig()
    if args.config:
        data = json.loads(Path(args.config).read_text(encoding="utf-8"))
        config = CurriculumConfig(**{k: v for k, v in data.items()
                                     if k in CurriculumConfig.__dataclass_fields__})

    runner = CurriculumRunner(lessons, config=config,
                              state_path=str(base / args.state))
    fail_state: dict = {}

    def on_event(event: dict) -> None:
        plan, result = event["plan"], event["result"]
        print(f"[{event['step']:02d}] {plan['lesson_id']:<28} "
              f"budget={plan['budget']:<6} polarity={plan['polarity']:<8} "
              f"→ {result['outcome']}")

    executor = lean_executor if args.executor == "lean" else mock_executor(args.mock, fail_state)
    summary = runner.run(executor, max_steps=args.max_steps, on_event=on_event)

    print("\n== 课程状态 ==")
    for row in runner.status():
        flags = []
        if row["trusted"]:
            flags.append("trusted")
        if row["mastered"]:
            flags.append("MASTERED")
        elif row["passed"]:
            flags.append("passed")
        if row["disproved"]:
            flags.append("disproved")
        if row["blocked_unknown"]:
            flags.append("blocked")
        print(f"  {row['lesson_id']:<28} attempts={row['attempts']:<3} "
              f"streak={row['consecutive_success']:<3} next_budget={row['next_budget']:<6} "
              f"{' '.join(flags)}")
    print(f"\nsteps={summary['steps']}  complete={runner.complete()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

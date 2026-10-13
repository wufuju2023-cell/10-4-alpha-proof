"""课程学习机制（curriculum）：课程序列 + 预算调度 + 三闸门。

对齐口径（N33 / 官方 Table 7 与本机 v1 规格）：
- 预算：B = min(cap, base * multiplier^f)，base=250、multiplier=1.17、cap=16000；
- 信任/掌握窗口：trust_count=8、mastery_count=12；
- 反证率：0.5（证明/反证双向调度，disproved 的课永久排除）；
- 三闸门（课程变体准入）：Lean 编译 / 难度 1-solve@16 ∈ [0.5,0.9] / 结构 Sim ≥ 0.7；
- 优先级权重（官方表 7 语义）：interesting 1.0 / undecided 0.1 / fully-proved 0.001 / disproved 0。

本模块只做“调度与判定”，不执行搜索与训练；执行器由调用方注入。
"""

from .gates import GateReport, evaluate_gates, solve_rate_from_results
from .models import (AttemptPlan, AttemptResult, CurriculumConfig, Lesson,
                     LessonState, OUTCOMES)
from .runner import CurriculumRunner
from .scheduler import Scheduler

__all__ = [
    "AttemptPlan", "AttemptResult", "CurriculumConfig", "Lesson", "LessonState",
    "OUTCOMES", "GateReport", "evaluate_gates", "solve_rate_from_results",
    "Scheduler", "CurriculumRunner",
]

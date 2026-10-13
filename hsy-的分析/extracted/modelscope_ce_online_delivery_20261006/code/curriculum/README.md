# curriculum/ · 课程学习机制

> 截至 2026-10-04 本轮新增。此前只有“单课测试素材”（Pell 七课），没有课程机制；
> 本目录补齐：**课程序列 + 依赖解锁 + 预算调度 + 掌握/反证判定 + 三闸门**。
> 只做调度与判定；搜索/训练由 executor 注入（真实执行入口见 `lean/pell_smoke/README.md`）。

## 对齐来源

| 机制 | 来源 | 本实现 |
| --- | --- | --- |
| 预算 B = min(cap, base×mult^f) | 官方 Table 7：250 / 1.17 / 16000 | `Scheduler.budget`（f=窗口内 exhausted 次数） |
| 信任/掌握窗口 | 官方 Table 7：trust=8 / trust_proved=12 | `trust_count` / `mastery_count` |
| 反证率 50% + 反证永久排除 | 官方 Table 7 + 伪码 | `polarity_for`（sha256 奇偶）+ `disproved` 标记 |
| 优先级权重 1.0 / 0.1 / 0.001 / 0 | 官方 Table 7 | `priority_weight`：interesting/undecided/fully-proved/disproved |
| 窗口内 exhausted 才涨预算、尝试少者优先 | v1 `cpu_runtime/matchmaker.py` | `budget` / `select`（公平性） |
| 三闸门（变体准入） | 本机 v1 规格 | `evaluate_gates`：编译 / 难度 1−solve@16∈[0.5,0.9] / 结构 Sim≥0.7 |

## 组件与 API

```python
from curriculum import (Lesson, CurriculumConfig, Scheduler, CurriculumRunner,
                        AttemptResult, evaluate_gates)

sched = Scheduler(CurriculumConfig())
budget = sched.budget("pell-01-invariant")        # 250
polarity = sched.polarity_for("pell-01-invariant") # prove / disprove（确定性）
lesson = sched.select(lessons)                     # 依赖未掌握 → 不会解锁下一课
sched.record(AttemptResult("pell-01-invariant", "solved"))
```

```python
runner = CurriculumRunner(lessons, state_path="outputs/curriculum_state.json")
runner.run(executor=my_executor, max_steps=50)      # executor(lesson, plan) -> AttemptResult
```

- `Executor` 由调用方注入：真实版可包装 Lean 搜索（`plan.budget` → 搜索预算）；
- 状态 JSON **原子写入**（tmp + `os.replace`），可断点续跑；
- `outcome ∈ {solved, exhausted, disproved, timeout, unknown}`；
  `unknown`（提交结果未知）会**冻结该课**等待对账，绝不当作数学失败重试（v1 纪律）。

## Pell 课程

`pell_course.json`：七课手写课程（来自已验收实验，非自动生成），依赖链：
`01-invariant → 02-growth → 03-iterates / 04-recurrence → 05-sequence-exists → 06-unbounded → 07-indexed-witness`。
其中第 1 课已在真实 Lean 内核上求解成功（见 `lean/pell_smoke/README.md`）。

## 演示

```bash
python3 scripts/run_curriculum.py --course curriculum/pell_course.json \
    --state outputs/curriculum_demo_state.json --max-steps 40 --mock fail-once
```

`--mock fail-once`：每课先 exhausted 一次再 solved（演示预算 250 → 292 增长与掌握推进）；
`--mock always-solve`：全部一次通过。`--executor lean` 为真实执行占位（需 Lean 环境）。

## 未实现（保持透明）

- **teacher 变体生成 / auto-formalization**：官方 Gemini 管线与本机 DeepSeek 三闸门管线均未接入；
  `evaluate_gates` 只做判定，不产生变体，`solve_rate_at_16` 与结构相似度需由调用方提供实测值；
- Matchmaker 的文件级持久化/多进程 lease/`unknown` 对账协议（v1 有完整实现）未搬迁；
  本目录是单进程、轻量版调度。

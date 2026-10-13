"""课程机制测试（纯 Python）：预算 / 优先级 / 掌握 / 反证 / 闸门 / 运行器持久化。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from curriculum import (AttemptPlan, AttemptResult, CurriculumConfig,  # noqa: E402
                        CurriculumRunner, Lesson, Scheduler, evaluate_gates,
                        solve_rate_from_results)


def test_budget_growth_and_cap():
    s = Scheduler(CurriculumConfig())
    lid = "L1"
    assert s.budget(lid) == 250
    s.record(AttemptResult(lid, "exhausted"))
    assert s.budget(lid) == 292                    # 250 * 1.17 = 292.5 → 截断
    s.record(AttemptResult(lid, "exhausted"))
    assert s.budget(lid) == 342                    # 250 * 1.17^2 ≈ 342.2
    # timeout 不涨预算
    s2 = Scheduler(CurriculumConfig())
    s2.record(AttemptResult("L2", "timeout"))
    assert s2.budget("L2") == 250
    # cap：默认 history_window=25 时，窗口内最多 25 次增长 → 12664（官方 cap 16000 需更大窗口）
    s3 = Scheduler(CurriculumConfig())
    for _ in range(40):
        s3.record(AttemptResult("L3", "exhausted"))
    assert s3.budget("L3") == 12664
    s4 = Scheduler(CurriculumConfig(history_window=50))
    for _ in range(40):
        s4.record(AttemptResult("L4", "exhausted"))
    assert s4.budget("L4") == 16000               # 250 * 1.17^40 远超 cap


def test_priority_weights_and_mastery():
    cfg = CurriculumConfig()
    s = Scheduler(cfg)
    lid = "L1"
    assert s.priority_weight(lid) == 0.1           # undecided
    s.record(AttemptResult(lid, "exhausted"))
    assert s.priority_weight(lid) == 1.0           # interesting
    for _ in range(cfg.mastery_count):
        s.record(AttemptResult(lid, "solved"))
    st = s.state(lid)
    assert st.mastered and st.trusted
    assert s.priority_weight(lid) == 0.001         # fully proved
    s.record(AttemptResult(lid, "disproved"))
    assert s.priority_weight(lid) == 0.0 and st.disproved


def test_trusted_before_mastery():
    cfg = CurriculumConfig()
    s = Scheduler(cfg)
    for _ in range(cfg.trust_count):
        s.record(AttemptResult("L", "solved"))
    st = s.state("L")
    assert st.trusted and not st.mastered
    s.record(AttemptResult("L", "exhausted"))
    assert not st.trusted and st.consecutive_success == 0


def test_unknown_blocks_and_disproved_excluded():
    lessons = [Lesson("A", "a", "ref"), Lesson("B", "b", "ref")]
    s = Scheduler()
    s.record(AttemptResult("A", "unknown"))
    selected = s.select(lessons, respect_dependencies=False)
    assert selected is not None and selected.lesson_id == "B"
    s.record(AttemptResult("B", "disproved"))
    assert s.select(lessons, respect_dependencies=False) is None


def test_dependency_unlocking():
    a = Lesson("A", "a", "ref")
    b = Lesson("B", "b", "ref", depends_on=("A",))
    s = Scheduler()
    assert s.select([a, b]) is a
    s.record(AttemptResult("A", "solved"))          # 默认 advance_streak=1 → 解锁下一课
    assert s.select([a, b]) is b


def test_strict_mastery_mode_keeps_dependency_locked():
    a = Lesson("A", "a", "ref")
    b = Lesson("B", "b", "ref", depends_on=("A",))
    cfg = CurriculumConfig(strict_mastery=True)
    s = Scheduler(cfg)
    s.record(AttemptResult("A", "solved"))
    assert s.select([a, b]) is a                    # 未掌握：B 仍锁定
    for _ in range(cfg.mastery_count - 1):
        s.record(AttemptResult("A", "solved"))
    assert s.state("A").mastered
    assert s.select([a, b]) is b


def test_polarity_deterministic_and_balanced():
    s = Scheduler(CurriculumConfig())
    count = sum(s.polarity_for(f"lesson-{i}") == "disprove" for i in range(200))
    assert 70 <= count <= 130                       # ≈50%，宽区间防哈希波动
    assert s.polarity_for("x") == s.polarity_for("x")


def test_gates_boundaries():
    cfg = CurriculumConfig(difficulty_lo=0.5, difficulty_hi=0.9, structure_min=0.7)
    ok = evaluate_gates(compiles=True, solve_rate_at_16=0.3, similarity=0.7, config=cfg)
    assert ok.admitted and ok.difficulty_ok and ok.structure_ok
    bad_compile = evaluate_gates(compiles=False, solve_rate_at_16=0.3, similarity=0.9, config=cfg)
    assert not bad_compile.admitted and not bad_compile.compile_ok
    too_hard = evaluate_gates(compiles=True, solve_rate_at_16=0.05, similarity=0.9, config=cfg)
    assert not too_hard.difficulty_ok               # 1-0.05=0.95 > 0.9
    too_easy = evaluate_gates(compiles=True, solve_rate_at_16=0.8, similarity=0.9, config=cfg)
    assert not too_easy.difficulty_ok               # 0.2 < 0.5
    low_sim = evaluate_gates(compiles=True, solve_rate_at_16=0.3, similarity=0.69, config=cfg)
    assert not low_sim.structure_ok
    assert solve_rate_from_results(3, 16) == 0.1875


def test_runner_persistence_and_resume(tmp_path):
    lessons = [Lesson("A", "a", "ref"), Lesson("B", "b", "ref", depends_on=("A",))]
    state_path = tmp_path / "state.json"
    runner = CurriculumRunner(lessons, state_path=str(state_path))
    attempts = {"A": 0, "B": 0}

    def executor(lesson, plan):
        attempts[lesson.lesson_id] += 1
        if lesson.lesson_id == "A":
            return AttemptResult("A", "solved")
        if attempts["B"] == 1:
            return AttemptResult("B", "exhausted")
        return AttemptResult("B", "solved")

    summary = runner.run(executor, max_steps=20)
    assert summary["steps"] == 3                    # A 一次 + B 两次
    assert runner.complete()
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert set(data["states"]) == {"A", "B"}
    # 断点续跑：状态恢复后直接完成，不再执行
    runner2 = CurriculumRunner(lessons, state_path=str(state_path))
    assert runner2.complete()
    assert runner2.run(executor, max_steps=5)["steps"] == 0

    # 预算增长确实在第二次 B 尝试生效（250 → 292）
    budgets = [e["plan"]["budget"] for e in summary["events"] if e["plan"]["lesson_id"] == "B"]
    assert budgets == [250, 292]

"""课程调度器：预算增长 + 优先级 + 证明/反证极性 + 掌握/排除判定。

语义来源：
- 官方 Table 7：base=250、mult=1.17、cap=16000、trust=8、trust_proved=12、disprove_rate=0.5、
  优先级权重 interesting 1.0 / undecided 0.1 / fully proved 0.001 / disproved 0；
- v1 `cpu_runtime/matchmaker.py`：预算只在窗口内 "exhausted" 时 ×multiplier；
  历史长度不足 trust_count 视为未信任；最近 mastery_count 全成功视为掌握；
  disproved 永久排除；公平性由“尝试次数少者优先”承担。
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional, Sequence

from .models import (AttemptResult, CurriculumConfig, Lesson, LessonState)


class Scheduler:
    def __init__(self, config: Optional[CurriculumConfig] = None,
                 states: Optional[Dict[str, LessonState]] = None) -> None:
        self.config = config or CurriculumConfig()
        self.states: Dict[str, LessonState] = states or {}

    # ------------------------------------------------------------------ #
    def state(self, lesson_id: str) -> LessonState:
        if lesson_id not in self.states:
            self.states[lesson_id] = LessonState(lesson_id=lesson_id)
        return self.states[lesson_id]

    # ------------------------------------------------------------------ #
    def budget(self, lesson_id: str) -> int:
        """B = min(cap, base × mult^f)，f = 近期窗口内 exhausted 次数。"""
        cfg = self.config
        history = self.state(lesson_id).history[-cfg.history_window:]
        fails = sum(1 for outcome in history if outcome == "exhausted")
        budget = cfg.budget_base * (cfg.budget_multiplier ** fails)
        return int(min(cfg.budget_cap, budget))

    # ------------------------------------------------------------------ #
    def priority_weight(self, lesson_id: str) -> float:
        """官方表 7 的优先级权重（越大越优先；disproved=0 直接排除）。"""
        st = self.state(lesson_id)
        if st.disproved:
            return 0.0
        if st.mastered:
            return 0.001           # fully proved
        if st.attempts == 0:
            return 0.1             # undecided
        return 1.0                 # interesting（含 trusted/mixed）

    # ------------------------------------------------------------------ #
    def polarity_for(self, lesson_id: str, salt: str = "") -> str:
        """确定性证明/反证极性：sha256(seed|id|salt) 奇偶 vs disprove_rate。"""
        cfg = self.config
        digest = hashlib.sha256(f"{cfg.seed}|{lesson_id}|{salt}".encode("utf-8")).hexdigest()
        value = int(digest[:8], 16) / 0xFFFFFFFF
        return "disprove" if value < cfg.disprove_rate else "prove"

    # ------------------------------------------------------------------ #
    def meets_advance(self, lesson_id: str) -> bool:
        """是否达到“推进/解锁”门槛：默认连续成功 ≥ advance_streak；
        strict_mastery=True 时要求掌握（mastery_count 次一致成功，官方口径）。"""
        st = self.state(lesson_id)
        if self.config.strict_mastery:
            return st.mastered
        return st.mastered or st.consecutive_success >= self.config.advance_streak

    def unlocked(self, lesson: Lesson) -> bool:
        return all(self.meets_advance(dep) for dep in lesson.depends_on)

    def select(self, lessons: Sequence[Lesson], respect_dependencies: bool = True,
               include_advanced: bool = False) -> Optional[Lesson]:
        """选下一课：排除已达到推进门槛/disproved/blocked 与未解锁的课；
        同等条件下尝试次数少者优先（公平性），哈希做确定性 tie-break。"""
        candidates = []
        for lesson in lessons:
            st = self.state(lesson.lesson_id)
            if st.disproved or st.blocked_unknown:
                continue
            if self.meets_advance(lesson.lesson_id) and not include_advanced:
                continue
            if respect_dependencies and not self.unlocked(lesson):
                continue
            weight = self.priority_weight(lesson.lesson_id)
            tie = hashlib.sha256(
                f"{self.config.seed}|{lesson.lesson_id}".encode("utf-8")
            ).hexdigest()
            candidates.append(((-weight, st.attempts, tie), lesson))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    # ------------------------------------------------------------------ #
    def record(self, result: AttemptResult) -> LessonState:
        st = self.state(result.lesson_id)
        st.attempts += 1
        st.history.append(result.outcome)
        cfg = self.config

        if result.outcome == "disproved":
            st.disproved = True
            st.consecutive_success = 0
            st.trusted = False
            st.mastered = False
            return st

        if result.outcome == "unknown":
            st.blocked_unknown = True
            st.consecutive_success = 0
            return st

        if result.outcome == "solved":
            st.consecutive_success += 1
            if st.consecutive_success >= cfg.trust_count:
                st.trusted = True
            if st.consecutive_success >= cfg.mastery_count:
                st.mastered = True
        else:  # exhausted / timeout：清零连续成功
            st.consecutive_success = 0
            st.trusted = False
        return st

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "states": {k: v.to_dict() for k, v in sorted(self.states.items())},
        }

    @staticmethod
    def restore(snapshot: dict) -> "Scheduler":
        config = CurriculumConfig(
            **{k: v for k, v in snapshot.get("config", {}).items()
               if k in CurriculumConfig.__dataclass_fields__}
        )
        states = {
            k: LessonState.from_dict(v) for k, v in snapshot.get("states", {}).items()
        }
        return Scheduler(config=config, states=states)

    def summary(self, lessons: Sequence[Lesson]) -> List[dict]:
        rows = []
        for lesson in lessons:
            st = self.state(lesson.lesson_id)
            rows.append({
                "lesson_id": lesson.lesson_id,
                "attempts": st.attempts,
                "consecutive_success": st.consecutive_success,
                "trusted": st.trusted,
                "mastered": st.mastered,
                "passed": self.meets_advance(lesson.lesson_id),
                "disproved": st.disproved,
                "blocked_unknown": st.blocked_unknown,
                "next_budget": self.budget(lesson.lesson_id),
                "priority": self.priority_weight(lesson.lesson_id),
            })
        return rows

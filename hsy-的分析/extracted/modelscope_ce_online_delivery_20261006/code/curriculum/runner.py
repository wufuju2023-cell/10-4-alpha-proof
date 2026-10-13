"""课程运行器：选课 → 预算 → 执行（executor 注入）→ 记录 → 原子持久化。

executor 由调用方提供，签名：
    executor(lesson: Lesson, plan: AttemptPlan) -> AttemptResult
真实执行器可包真实 Lean 搜索（见 lean/pell_smoke/README.md）；测试用模拟执行器。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .models import (AttemptPlan, AttemptResult, CurriculumConfig, Lesson,
                     LessonState)
from .scheduler import Scheduler

Executor = Callable[[Lesson, AttemptPlan], AttemptResult]


class CurriculumRunner:
    def __init__(self, lessons: Sequence[Lesson],
                 config: Optional[CurriculumConfig] = None,
                 state_path: Optional[str] = None) -> None:
        self.lessons: List[Lesson] = list(lessons)
        self.config = config or CurriculumConfig()
        self.state_path = Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            snapshot = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.scheduler = Scheduler.restore(snapshot)
        else:
            self.scheduler = Scheduler(self.config)

    # ------------------------------------------------------------------ #
    def run(self, executor: Executor, max_steps: int = 1000,
            on_event: Optional[Callable[[dict], None]] = None) -> dict:
        events: List[dict] = []
        for step in range(max_steps):
            lesson = self.scheduler.select(self.lessons)
            if lesson is None:
                break
            plan = AttemptPlan(
                lesson_id=lesson.lesson_id,
                budget=self.scheduler.budget(lesson.lesson_id),
                polarity=self.scheduler.polarity_for(lesson.lesson_id, str(step)),
            )
            result = executor(lesson, plan)
            state = self.scheduler.record(result)
            event = {
                "step": step,
                "plan": asdict(plan),
                "result": result.to_dict(),
                "state": state.to_dict(),
            }
            events.append(event)
            if on_event is not None:
                on_event(event)
            self.save()
        return {"steps": len(events), "events": events, "status": self.status()}

    # ------------------------------------------------------------------ #
    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.scheduler.snapshot(), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.state_path)

    def status(self) -> List[dict]:
        return self.scheduler.summary(self.lessons)

    def complete(self) -> bool:
        return self.scheduler.select(self.lessons) is None

    @staticmethod
    def load_lessons(path: str) -> List[Lesson]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("lessons", [])
        return [Lesson.from_dict(item) for item in data]

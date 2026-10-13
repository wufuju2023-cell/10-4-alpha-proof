from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable

from .schema import RolloutSample, SearchObservation


def compute_search_advantages(
    observations: Iterable[SearchObservation],
    *,
    epsilon: float = 1e-6,
    clip: float = 5.0,
) -> dict[tuple[str, str, str], float]:
    """Standardize action Q values only against alternatives at the same state.

    A state with fewer than two distinct Q values carries no comparative policy
    signal and receives zero advantage. This avoids inventing negative labels
    for an unresolved trajectory merely because it eventually timed out.
    """

    groups: dict[tuple[str, str], list[SearchObservation]] = defaultdict(list)
    for item in observations:
        item.validate()
        groups[(item.problem_id, item.state_id)].append(item)

    result: dict[tuple[str, str, str], float] = {}
    for (problem_id, state_id), items in groups.items():
        values = [float(item.q_value) for item in items]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = math.sqrt(variance)
        for item in items:
            advantage = 0.0 if std <= epsilon else (item.q_value - mean) / (std + epsilon)
            result[(problem_id, state_id, item.action)] = max(-clip, min(clip, advantage))
    return result


class ProblemGroupedBuffer:
    """Collect one policy-version wave and gate readiness by independent problems."""

    def __init__(self, policy_version: str, min_problems: int, max_events_per_problem: int) -> None:
        if not policy_version:
            raise ValueError("policy_version must be non-empty")
        if min_problems < 1 or max_events_per_problem < 1:
            raise ValueError("buffer limits must be positive")
        self.policy_version = policy_version
        self.min_problems = min_problems
        self.max_events_per_problem = max_events_per_problem
        self._by_problem: dict[str, list[RolloutSample]] = defaultdict(list)

    def submit(self, sample: RolloutSample) -> bool:
        sample.validate()
        if sample.policy_version != self.policy_version:
            raise ValueError(
                f"stale/mixed policy version: expected {self.policy_version}, got {sample.policy_version}"
            )
        bucket = self._by_problem[sample.problem_id]
        if len(bucket) >= self.max_events_per_problem:
            return False
        bucket.append(sample)
        return True

    @property
    def independent_problem_count(self) -> int:
        return len(self._by_problem)

    @property
    def ready(self) -> bool:
        return self.independent_problem_count >= self.min_problems

    def drain(self) -> list[RolloutSample]:
        if not self.ready:
            raise RuntimeError(
                f"wave has {self.independent_problem_count} independent problems; "
                f"requires {self.min_problems}"
            )
        samples = [sample for items in self._by_problem.values() for sample in items]
        self._by_problem.clear()
        return samples

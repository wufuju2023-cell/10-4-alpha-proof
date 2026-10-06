from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable, Sequence

import torch

from .schema import RolloutSample, SearchObservation


def compute_search_advantages(
    observations: Iterable[SearchObservation],
    *,
    epsilon: float = 1e-6,
    clip: float = 5.0,
) -> dict[tuple[str, str, str], tuple[float, float]]:
    """Return ``(baseline, advantage)`` from equally weighted raw draws.

    ``sample_multiplicity`` restores the count of raw draws when byte-identical
    draws were collapsed. It is a count, never a theoretical q(x) weight.
    Advantages use the corresponding draw-weighted standard deviation. A state
    with no comparative Q evidence gets zero advantage.
    """

    if clip <= 0:
        raise ValueError("clip must be positive")
    groups: dict[tuple[str, str], list[SearchObservation]] = defaultdict(list)
    for item in observations:
        item.validate()
        groups[(item.problem_id, item.state_id)].append(item)

    result: dict[tuple[str, str, str], tuple[float, float]] = {}
    for (problem_id, state_id), items in groups.items():
        if len({item.action for item in items}) != len(items):
            raise ValueError(f"duplicate action in state {problem_id}/{state_id}")
        weight_sum = sum(item.sample_multiplicity for item in items)
        weights = [item.sample_multiplicity / weight_sum for item in items]
        baseline = sum(weight * item.action_value for weight, item in zip(weights, items))
        variance = sum(
            weight * (item.action_value - baseline) ** 2
            for weight, item in zip(weights, items)
        )
        scale = math.sqrt(variance)
        for item in items:
            raw = item.action_value - baseline
            advantage = 0.0 if scale <= epsilon else raw / (scale + epsilon)
            result[(problem_id, state_id, item.action)] = (
                baseline,
                max(-clip, min(clip, advantage)),
            )
    return result


def problem_balanced_weights(
    problem_ids: Sequence[str], multiplicities: Sequence[int] | None = None,
) -> torch.Tensor:
    """Give each problem equal mass and preserve collapsed raw-draw counts."""

    if not problem_ids or any(not item for item in problem_ids):
        raise ValueError("problem_ids must be non-empty")
    if multiplicities is None:
        multiplicities = [1] * len(problem_ids)
    if len(multiplicities) != len(problem_ids) or any(type(value) is not int or value < 1 for value in multiplicities):
        raise ValueError("multiplicities must be positive integers aligned with problem_ids")
    counts: dict[str, int] = defaultdict(int)
    for problem_id, multiplicity in zip(problem_ids, multiplicities, strict=True):
        counts[problem_id] += multiplicity
    problem_mass = 1.0 / len(counts)
    return torch.tensor(
        [problem_mass * multiplicity / counts[problem_id]
         for problem_id, multiplicity in zip(problem_ids, multiplicities, strict=True)],
        dtype=torch.float32,
    )


class ProblemGroupedBuffer:
    """Collect exactly one version-bound rollout wave, gated by problems."""

    def __init__(
        self,
        policy_version: str,
        min_problems: int,
        max_events_per_problem: int,
        max_events_per_trajectory: int = 8,
    ) -> None:
        if not policy_version:
            raise ValueError("policy_version must be non-empty")
        if min_problems < 1 or max_events_per_problem < 1 or max_events_per_trajectory < 1:
            raise ValueError("buffer limits must be positive")
        self.policy_version = policy_version
        self.min_problems = min_problems
        self.max_events_per_problem = max_events_per_problem
        self.max_events_per_trajectory = max_events_per_trajectory
        self._by_problem: dict[str, list[RolloutSample]] = defaultdict(list)
        self._trajectory_counts: dict[tuple[str, str], int] = defaultdict(int)

    def submit(self, sample: RolloutSample) -> bool:
        sample.validate()
        if sample.policy_version != self.policy_version:
            raise ValueError(
                f"stale/mixed policy version: expected {self.policy_version}, got {sample.policy_version}"
            )
        bucket = self._by_problem[sample.problem_id]
        if len(bucket) >= self.max_events_per_problem:
            return False
        trajectory_key = (sample.problem_id, sample.trajectory_id)
        if self._trajectory_counts[trajectory_key] >= self.max_events_per_trajectory:
            return False
        bucket.append(sample)
        self._trajectory_counts[trajectory_key] += 1
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
        self._trajectory_counts.clear()
        return samples

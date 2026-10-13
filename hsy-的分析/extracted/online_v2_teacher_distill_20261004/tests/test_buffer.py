from __future__ import annotations

import copy

import pytest

from alphaproof_online_v2.buffer import ProblemGroupedBuffer, compute_search_advantages
from alphaproof_online_v2.schema import SearchObservation

from helpers import TinyPolicy, make_sample


def test_advantages_compare_actions_only_within_same_state() -> None:
    observations = [
        SearchObservation("p1", "s1", "good", 1.0),
        SearchObservation("p1", "s1", "bad", 0.0),
        SearchObservation("p2", "s2", "only", 0.3),
    ]
    result = compute_search_advantages(observations)
    assert result[("p1", "s1", "good")] > 0
    assert result[("p1", "s1", "bad")] < 0
    assert result[("p2", "s2", "only")] == 0.0


def test_buffer_counts_problems_and_rejects_policy_mixing() -> None:
    model = TinyPolicy()
    buffer = ProblemGroupedBuffer("v1", min_problems=2, max_events_per_problem=1)
    first = make_sample(model, problem_id="p1", trajectory_id="t1", policy_version="v1")
    assert buffer.submit(first)
    assert not buffer.submit(copy.deepcopy(first))
    assert not buffer.ready
    second = make_sample(model, problem_id="p2", trajectory_id="t2", policy_version="v1")
    assert buffer.submit(second)
    assert buffer.ready
    assert len(buffer.drain()) == 2
    stale = make_sample(model, problem_id="p3", trajectory_id="t3", policy_version="v0")
    with pytest.raises(ValueError, match="stale/mixed"):
        buffer.submit(stale)

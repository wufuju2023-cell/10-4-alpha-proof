from __future__ import annotations

import copy

import pytest
import torch

from alphaproof_online_v2_arm.buffer import (
    ProblemGroupedBuffer,
    compute_search_advantages,
    problem_balanced_weights,
)
from alphaproof_online_v2_arm.rewards import VerificationRecord, strict_local_reward
from alphaproof_online_v2_arm.schema import SearchObservation

from helpers import TinyPolicy, make_sample


def test_advantage_uses_raw_draw_multiplicity_not_theoretical_q() -> None:
    result = compute_search_advantages(
        [
            SearchObservation("p", "s", "good", 1.0, 4),
            SearchObservation("p", "s", "bad", 0.0, 1),
            SearchObservation("p2", "s2", "only", 0.4, 1),
        ]
    )
    baseline, good = result[("p", "s", "good")]
    _, bad = result[("p", "s", "bad")]
    assert baseline == pytest.approx(0.8)
    assert good > 0 and bad < 0
    assert result[("p2", "s2", "only")][1] == 0.0


def test_problem_weights_do_not_let_many_events_dominate() -> None:
    weights = problem_balanced_weights(["p1", "p1", "p1", "p2"])
    assert torch.isclose(weights[:3].sum(), torch.tensor(0.5))
    assert torch.isclose(weights[3], torch.tensor(0.5))


def test_problem_weights_preserve_raw_multiplicity_without_q_weighting() -> None:
    weights = problem_balanced_weights(
        ["p1", "p1", "p2"], multiplicities=[3, 1, 9]
    )
    assert torch.allclose(weights, torch.tensor([0.375, 0.125, 0.5]))


def test_buffer_counts_problems_and_caps_trajectory() -> None:
    model = TinyPolicy()
    buffer = ProblemGroupedBuffer(
        "policy-0001", min_problems=2, max_events_per_problem=3, max_events_per_trajectory=1
    )
    first = make_sample(model, problem_id="p1", trajectory_id="t1")
    assert buffer.submit(first)
    assert not buffer.submit(copy.deepcopy(first))
    second = make_sample(model, problem_id="p2", trajectory_id="t2")
    assert buffer.submit(second)
    assert buffer.ready
    assert len(buffer.drain()) == 2


def test_reward_requires_strict_receipt_and_never_punishes_timeout() -> None:
    assert strict_local_reward(VerificationRecord("timeout", False, "r1")) == 0.0
    assert strict_local_reward(VerificationRecord("infrastructure_error", False, "r2")) == 0.0
    assert strict_local_reward(VerificationRecord("invalid_tactic", False, "r3")) < 0.0
    assert strict_local_reward(VerificationRecord("verified_proof", True, "r4")) == 1.0
    with pytest.raises(ValueError, match="missing strict"):
        strict_local_reward(VerificationRecord("verified_proof", False, "r5"))

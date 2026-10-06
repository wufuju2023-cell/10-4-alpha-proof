from __future__ import annotations

import math

import pytest
import torch

from alphaproof_online_v2_arm.objectives import (
    exact_forward_kl,
    selected_action_logits,
    selected_exact_forward_kl,
    selected_warped_token_logprobs,
    sequence_clipped_ppo_loss,
    two_hot_distance,
    weighted_categorical_value_loss,
)


def test_sequence_ratio_is_product_of_token_ratios() -> None:
    # Two token ratios exp(log(1.1)) produce one sequence ratio 1.21, which is
    # clipped to 1.2. An incorrect token-wise objective would return 1.1.
    delta = math.log(1.1)
    result = sequence_clipped_ppo_loss(
        torch.tensor([[delta, delta]]),
        torch.zeros((1, 2)),
        torch.ones((1, 2), dtype=torch.bool),
        torch.tensor([1.0]),
        torch.tensor([1.0]),
        clip_epsilon=0.2,
    )
    assert torch.isclose(result.loss, result.loss.new_tensor(-1.2), atol=1e-6)
    assert result.clip_fraction.item() == 1.0


def test_negative_advantage_uses_correct_ppo_clip_side() -> None:
    result = sequence_clipped_ppo_loss(
        torch.tensor([[-1.0]]),
        torch.zeros((1, 1)),
        torch.ones((1, 1), dtype=torch.bool),
        torch.tensor([-1.0]),
        torch.tensor([1.0]),
        clip_epsilon=0.2,
    )
    assert torch.isclose(result.loss, result.loss.new_tensor(0.8), atol=1e-6)


@pytest.mark.parametrize(("advantage", "expected_loss"), [(1.0, 0.0), (-1.0, 0.8)])
def test_zero_new_sampling_support_is_zero_ratio_without_nan(advantage: float, expected_loss: float) -> None:
    new_logprobs = torch.tensor([[-float("inf")]], requires_grad=True)
    result = sequence_clipped_ppo_loss(
        new_logprobs, torch.tensor([[-1.0]]), torch.ones((1, 1), dtype=torch.bool),
        torch.tensor([advantage]), torch.tensor([1.0]), clip_epsilon=0.2,
    )
    result.loss.backward()
    assert torch.isfinite(result.loss)
    assert torch.isfinite(result.approx_kl)
    assert result.loss.item() == pytest.approx(expected_loss)
    assert new_logprobs.grad is not None and torch.isfinite(new_logprobs.grad).all()
    assert new_logprobs.grad.item() == 0.0


def test_top_p_excluded_new_action_has_exact_zero_ratio_and_finite_gradient() -> None:
    logits = torch.tensor([[10.0, 0.0]], requires_grad=True)
    input_ids = torch.tensor([[0, 1]])
    action_mask = torch.tensor([[False, True]])
    new_q_logprob = selected_warped_token_logprobs(
        logits, input_ids, action_mask, temperature=1.5, top_p=0.9,
    )
    assert torch.isneginf(new_q_logprob).all()
    result = sequence_clipped_ppo_loss(
        new_q_logprob.reshape(1, 1), torch.tensor([[-0.4]]),
        torch.ones((1, 1), dtype=torch.bool), torch.tensor([1.0]),
        torch.tensor([1.0]), clip_epsilon=0.2,
    )
    result.loss.backward()
    assert result.loss.item() == 0.0
    assert logits.grad is not None and torch.isfinite(logits.grad).all()


@pytest.mark.parametrize(
    ("advantage", "log_ratio", "expected_loss", "expected_gradient"),
    [
        # Positive advantage: the low-ratio side remains active, while the
        # high-ratio side is the intentionally flat clipped branch.
        (1.0, -30.0, -math.exp(-30.0), -math.exp(-30.0)),
        (1.0, 30.0, -1.2, 0.0),
        # Negative advantage: the low-ratio side is clipped, while the
        # high-ratio side must retain its corrective gradient.
        (-1.0, -30.0, 0.8, 0.0),
        (-1.0, 30.0, math.exp(30.0), math.exp(30.0)),
    ],
)
def test_extreme_ratio_gradient_in_all_ppo_quadrants(
    advantage: float,
    log_ratio: float,
    expected_loss: float,
    expected_gradient: float,
) -> None:
    new_logprobs = torch.tensor([[log_ratio]], dtype=torch.float64, requires_grad=True)
    result = sequence_clipped_ppo_loss(
        new_logprobs,
        torch.zeros_like(new_logprobs),
        torch.ones_like(new_logprobs, dtype=torch.bool),
        torch.tensor([advantage], dtype=torch.float64),
        torch.tensor([1.0], dtype=torch.float64),
        clip_epsilon=0.2,
    )

    result.loss.backward()

    assert math.isfinite(result.loss.item())
    assert result.loss.item() == pytest.approx(expected_loss, rel=1e-12, abs=1e-15)
    assert new_logprobs.grad is not None
    assert new_logprobs.grad.item() == pytest.approx(expected_gradient, rel=1e-12, abs=1e-15)


def test_full_vocab_kl_is_zero_for_identical_policy() -> None:
    logits = torch.randn(2, 4, 7)
    mask = torch.tensor([[False, False, True, True], [False, True, True, False]])
    kl = exact_forward_kl(logits, logits.clone(), mask, torch.tensor([0.5, 0.5]))
    assert abs(kl.item()) < 1e-7


def test_selected_context_kl_matches_dense_reference_implementation() -> None:
    policy = torch.randn(2, 5, 7)
    reference = torch.randn(2, 5, 7)
    mask = torch.tensor(
        [[False, False, True, True, False], [False, True, True, True, False]]
    )
    weights = torch.tensor([0.25, 0.75])
    dense = exact_forward_kl(policy, reference, mask, weights)
    selected = selected_exact_forward_kl(
        selected_action_logits(policy, mask),
        selected_action_logits(reference, mask),
        mask,
        weights,
    )
    assert torch.allclose(dense, selected, atol=1e-7, rtol=1e-7)


def test_value_gradient_gives_each_problem_equal_total_mass() -> None:
    # Three events from p1 share half the mass; the single p2 event gets the
    # other half. With zero logits and different targets, the aggregate target
    # gradients make this equality directly observable.
    logits = torch.zeros((4, 4), requires_grad=True)
    distances = torch.tensor([1.0, 1.0, 1.0, 2.0])
    weights = torch.tensor([1 / 6, 1 / 6, 1 / 6, 1 / 2])
    loss = weighted_categorical_value_loss(logits, distances, weights)
    loss.backward()
    assert logits.grad is not None
    p1_target_gradient = logits.grad[:3, 0].abs().sum()
    p2_target_gradient = logits.grad[3, 1].abs()
    assert torch.isclose(p1_target_gradient, p2_target_gradient, atol=1e-7)


def test_two_hot_distance_preserves_legal_boundaries_and_interpolation() -> None:
    target = two_hot_distance(torch.tensor([1.0, 1.5, 64.0]))
    assert target.shape == (3, 64)
    assert torch.equal(target[0], torch.nn.functional.one_hot(torch.tensor(0), 64).float())
    assert target[1, 0].item() == pytest.approx(0.5)
    assert target[1, 1].item() == pytest.approx(0.5)
    assert torch.equal(target[2], torch.nn.functional.one_hot(torch.tensor(63), 64).float())


@pytest.mark.parametrize("bad", [0.0, 64.0001, 65.0, float("inf"), float("nan")])
def test_two_hot_distance_rejects_out_of_range_instead_of_clamping(bad: float) -> None:
    with pytest.raises(ValueError, match="distance"):
        two_hot_distance(torch.tensor([bad]))

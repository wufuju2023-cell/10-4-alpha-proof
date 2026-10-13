from __future__ import annotations

import torch

from alphaproof_online_v2.objectives import (
    categorical_value_loss,
    clipped_ppo_loss,
    exact_forward_kl,
    two_hot_distance,
)


def test_positive_advantage_clips_large_ratio() -> None:
    new = torch.tensor([[0.5]])
    old = torch.tensor([[0.0]])
    mask = torch.tensor([[True]])
    advantage = torch.tensor([1.0])
    result = clipped_ppo_loss(new, old, mask, advantage, clip_epsilon=0.2)
    assert torch.isclose(result.loss, torch.tensor(-1.2))
    assert result.clip_fraction.item() == 1.0


def test_exact_kl_is_zero_for_identical_policies() -> None:
    logits = torch.randn(2, 4, 7)
    mask = torch.tensor([[False, False, True, True], [False, True, True, False]])
    kl = exact_forward_kl(logits, logits.clone(), mask)
    assert abs(kl.item()) < 1e-7


def test_two_hot_value_target_preserves_fractional_distance() -> None:
    target = two_hot_distance(torch.tensor([2.25]), bins=4)
    assert torch.allclose(target, torch.tensor([[0.0, 0.75, 0.25, 0.0]]))
    logits = target.clamp_min(1e-6).log()
    assert categorical_value_loss(logits, torch.tensor([2.25])).item() < 0.57

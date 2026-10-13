from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F


@dataclass(frozen=True)
class PPODiagnostics:
    loss: torch.Tensor
    approx_kl: torch.Tensor
    clip_fraction: torch.Tensor
    mean_ratio: torch.Tensor


def token_logprobs(logits: torch.Tensor, input_ids: torch.Tensor) -> torch.Tensor:
    """Return log p(x_t | x_<t) aligned to input positions 1..L-1."""

    if logits.ndim != 3 or input_ids.ndim != 2:
        raise ValueError("logits must be [B,L,V] and input_ids [B,L]")
    if logits.shape[:2] != input_ids.shape:
        raise ValueError("logits and input_ids sequence dimensions must match")
    log_probs = F.log_softmax(logits[:, :-1, :].float(), dim=-1)
    return log_probs.gather(-1, input_ids[:, 1:].unsqueeze(-1)).squeeze(-1)


def clipped_ppo_loss(
    new_logprobs: torch.Tensor,
    old_logprobs: torch.Tensor,
    action_mask: torch.Tensor,
    advantages: torch.Tensor,
    clip_epsilon: float,
) -> PPODiagnostics:
    """Token PPO with tactic-level advantages and per-tactic normalization."""

    if new_logprobs.shape != old_logprobs.shape or new_logprobs.shape != action_mask.shape:
        raise ValueError("PPO token tensors must share shape [B,L-1]")
    if advantages.shape != (new_logprobs.shape[0],):
        raise ValueError("advantages must be [B]")
    mask = action_mask.to(dtype=torch.bool)
    if not bool(mask.any().item()):
        raise ValueError("PPO action mask is empty")
    log_ratio = (new_logprobs - old_logprobs).clamp(-20.0, 20.0)
    ratio = log_ratio.exp()
    advantage = advantages[:, None]
    unclipped = ratio * advantage
    clipped_ratio = ratio.clamp(1.0 - clip_epsilon, 1.0 + clip_epsilon)
    clipped = clipped_ratio * advantage
    surrogate = torch.minimum(unclipped, clipped)
    counts = mask.sum(dim=1).clamp_min(1)
    per_tactic = (surrogate * mask).sum(dim=1) / counts
    selected_log_ratio = log_ratio[mask]
    selected_ratio = ratio[mask]
    approx_kl = ((selected_ratio - 1.0) - selected_log_ratio).mean()
    clip_fraction = ((selected_ratio - 1.0).abs() > clip_epsilon).float().mean()
    return PPODiagnostics(
        loss=-per_tactic.mean(),
        approx_kl=approx_kl,
        clip_fraction=clip_fraction,
        mean_ratio=selected_ratio.mean(),
    )


def exact_forward_kl(
    policy_logits: torch.Tensor,
    reference_logits: torch.Tensor,
    action_mask: torch.Tensor,
) -> torch.Tensor:
    """Full-vocabulary KL(policy || frozen reference) on action-token contexts."""

    if policy_logits.shape != reference_logits.shape:
        raise ValueError("policy/reference logits must have identical shape")
    aligned_mask = action_mask[:, 1:].to(dtype=torch.bool)
    if not bool(aligned_mask.any().item()):
        raise ValueError("KL action mask is empty")
    policy_logp = F.log_softmax(policy_logits[:, :-1, :].float(), dim=-1)
    reference_logp = F.log_softmax(reference_logits[:, :-1, :].float(), dim=-1)
    policy_prob = policy_logp.exp()
    per_position = (policy_prob * (policy_logp - reference_logp)).sum(dim=-1)
    return per_position[aligned_mask].mean()


def masked_entropy(logits: torch.Tensor, action_mask: torch.Tensor) -> torch.Tensor:
    aligned_mask = action_mask[:, 1:].to(dtype=torch.bool)
    logp = F.log_softmax(logits[:, :-1, :].float(), dim=-1)
    entropy = -(logp.exp() * logp).sum(dim=-1)
    return entropy[aligned_mask].mean()


def two_hot_distance(distance: torch.Tensor, bins: int = 64) -> torch.Tensor:
    """Encode non-negative remaining-step distances into bins 1..bins."""

    if bins < 2:
        raise ValueError("bins must be at least 2")
    d = distance.float().clamp(1.0, float(bins))
    lower = d.floor().long()
    upper = (lower + 1).clamp_max(bins)
    fraction = d - lower.float()
    target = torch.zeros((*d.shape, bins), device=d.device, dtype=torch.float32)
    target.scatter_add_(-1, (lower - 1).unsqueeze(-1), (1.0 - fraction).unsqueeze(-1))
    target.scatter_add_(-1, (upper - 1).unsqueeze(-1), fraction.unsqueeze(-1))
    return target


def categorical_value_loss(value_logits: torch.Tensor, distances: torch.Tensor) -> torch.Tensor:
    target = two_hot_distance(distances, bins=value_logits.shape[-1])
    return -(target * F.log_softmax(value_logits.float(), dim=-1)).sum(dim=-1).mean()

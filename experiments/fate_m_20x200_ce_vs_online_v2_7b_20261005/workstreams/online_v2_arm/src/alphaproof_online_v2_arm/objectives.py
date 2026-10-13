from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn.functional as F


@dataclass(frozen=True)
class PPODiagnostics:
    loss: torch.Tensor
    approx_kl: torch.Tensor
    clip_fraction: torch.Tensor
    mean_ratio: torch.Tensor
    mean_log_ratio: torch.Tensor


def token_logprobs(logits: torch.Tensor, input_ids: torch.Tensor) -> torch.Tensor:
    """Return log p(x_t | x_<t), aligned to input positions 1..L-1."""

    if logits.ndim != 3 or input_ids.ndim != 2:
        raise ValueError("logits must be [B,L,V] and input_ids [B,L]")
    if logits.shape[:2] != input_ids.shape:
        raise ValueError("logits and input_ids sequence dimensions must match")
    log_probs = F.log_softmax(logits[:, :-1, :].float(), dim=-1)
    return log_probs.gather(-1, input_ids[:, 1:].unsqueeze(-1)).squeeze(-1)


def selected_action_logits(logits: torch.Tensor, action_mask: torch.Tensor) -> torch.Tensor:
    """Materialize vocabulary logits only for contexts that predict action tokens."""

    if logits.ndim != 3 or action_mask.ndim != 2 or logits.shape[:2] != action_mask.shape:
        raise ValueError("logits [B,L,V] and action_mask [B,L] must align")
    aligned = action_mask[:, 1:].to(dtype=torch.bool)
    if not bool(aligned.any(dim=1).all().item()):
        raise ValueError("every sample needs an action context")
    return logits[:, :-1, :][aligned]


def selected_token_logprobs(
    selected_logits: torch.Tensor,
    input_ids: torch.Tensor,
    action_mask: torch.Tensor,
) -> torch.Tensor:
    """Token log-probabilities for flattened selected action contexts only."""

    aligned = action_mask[:, 1:].to(dtype=torch.bool)
    targets = input_ids[:, 1:][aligned]
    if selected_logits.ndim != 2 or selected_logits.shape[0] != targets.numel():
        raise ValueError("selected logits do not match action-token count")
    return F.log_softmax(selected_logits.float(), dim=-1).gather(
        -1, targets.unsqueeze(-1)
    ).squeeze(-1)


def selected_warped_token_logprobs(
    selected_logits: torch.Tensor,
    input_ids: torch.Tensor,
    action_mask: torch.Tensor,
    *,
    temperature: float,
    top_p: float,
) -> torch.Tensor:
    """Log q(token) under generation's temperature-then-top-p distribution."""

    if not math.isfinite(temperature) or temperature <= 0 or not 0 < top_p <= 1:
        raise ValueError("invalid sampling temperature/top_p")
    aligned = action_mask[:, 1:].to(dtype=torch.bool)
    targets = input_ids[:, 1:][aligned]
    if selected_logits.ndim != 2 or selected_logits.shape[0] != targets.numel():
        raise ValueError("selected logits do not match action-token count")
    scores = selected_logits.float() / temperature
    sorted_logits, sorted_indices = torch.sort(scores, descending=True, dim=-1)
    cumulative = torch.softmax(sorted_logits, dim=-1).cumsum(dim=-1)
    remove = cumulative > top_p
    remove[..., 1:] = remove[..., :-1].clone()
    remove[..., 0] = False
    sorted_logits = sorted_logits.masked_fill(remove, float("-inf"))
    warped = torch.full_like(scores, float("-inf")).scatter(-1, sorted_indices, sorted_logits)
    return F.log_softmax(warped, dim=-1).gather(-1, targets.unsqueeze(-1)).squeeze(-1)


def _selected_per_sample_mean(
    per_position: torch.Tensor,
    action_mask: torch.Tensor,
) -> torch.Tensor:
    aligned = action_mask[:, 1:].to(dtype=torch.bool)
    rows = torch.arange(aligned.shape[0], device=aligned.device)[:, None].expand_as(aligned)
    selected_rows = rows[aligned]
    sums = torch.zeros(aligned.shape[0], device=per_position.device, dtype=per_position.dtype)
    sums.scatter_add_(0, selected_rows, per_position)
    return sums / aligned.sum(dim=1).to(per_position.dtype)


def selected_exact_forward_kl(
    policy_selected_logits: torch.Tensor,
    reference_selected_logits: torch.Tensor,
    action_mask: torch.Tensor,
    sample_weights: torch.Tensor,
) -> torch.Tensor:
    """Full-vocabulary forward KL on flattened sampled action prefixes."""

    if policy_selected_logits.shape != reference_selected_logits.shape:
        raise ValueError("selected policy/reference logits must match")
    policy_logp = F.log_softmax(policy_selected_logits.float(), dim=-1)
    reference_logp = F.log_softmax(reference_selected_logits.float(), dim=-1)
    per_position = (policy_logp.exp() * (policy_logp - reference_logp)).sum(dim=-1)
    per_sample = _selected_per_sample_mean(per_position, action_mask)
    weights = sample_weights.float() / sample_weights.sum().clamp_min(1e-12)
    return (weights * per_sample).sum()


def selected_masked_entropy(
    selected_logits: torch.Tensor,
    action_mask: torch.Tensor,
    sample_weights: torch.Tensor,
) -> torch.Tensor:
    logp = F.log_softmax(selected_logits.float(), dim=-1)
    per_position = -(logp.exp() * logp).sum(dim=-1)
    per_sample = _selected_per_sample_mean(per_position, action_mask)
    weights = sample_weights.float() / sample_weights.sum().clamp_min(1e-12)
    return (weights * per_sample).sum()


def sequence_clipped_ppo_loss(
    new_logprobs: torch.Tensor,
    old_logprobs: torch.Tensor,
    action_mask: torch.Tensor,
    advantages: torch.Tensor,
    sample_weights: torch.Tensor,
    clip_epsilon: float,
) -> PPODiagnostics:
    """PPO for one complete raw token completion as a sequence action.

    The importance ratio is ``pi_new(a|s)/pi_old(a|s)``, hence the product of
    token ratios (implemented as a sum in log space). This intentionally avoids
    calling an average token ratio a sequence-policy ratio. Each problem has
    equal total weight through ``sample_weights``.
    """

    if new_logprobs.shape != old_logprobs.shape or new_logprobs.shape != action_mask.shape:
        raise ValueError("PPO token tensors must share shape [B,L-1]")
    batch = new_logprobs.shape[0]
    if advantages.shape != (batch,) or sample_weights.shape != (batch,):
        raise ValueError("advantages and sample_weights must be [B]")
    if not 0.0 < clip_epsilon < 1.0:
        raise ValueError("clip_epsilon must be in (0,1)")
    mask = action_mask.to(dtype=torch.bool)
    if not bool(mask.any(dim=1).all().item()):
        raise ValueError("every sample needs at least one action token")
    weights = sample_weights.float()
    if not torch.isfinite(weights).all() or bool((weights < 0).any().item()):
        raise ValueError("sample_weights must be finite and non-negative")
    weights = weights / weights.sum().clamp_min(1e-12)
    sequence_log_ratio = ((new_logprobs - old_logprobs) * mask).sum(dim=1)
    if bool(torch.isnan(sequence_log_ratio).any().item()) or bool(torch.isposinf(sequence_log_ratio).any().item()):
        raise ValueError("sequence log-ratio must be finite or negative infinity")

    # Select the exact PPO branch in log space before exponentiating.  For a
    # positive advantage only the upper side is clipped; for a negative
    # advantage only the lower side is clipped.  A symmetric log-ratio clamp
    # would incorrectly erase the gradient on the other, active side.
    work_log_ratio = sequence_log_ratio.double()
    work_advantages = advantages.double()
    lower_clip_log = math.log1p(-clip_epsilon)
    upper_clip_log = math.log1p(clip_epsilon)
    effective_log_ratio = torch.where(
        work_advantages >= 0.0,
        torch.minimum(work_log_ratio, work_log_ratio.new_tensor(upper_clip_log)),
        torch.maximum(work_log_ratio, work_log_ratio.new_tensor(lower_clip_log)),
    )

    # Outside the exponent range representable by the source dtype, cap the
    # forward value but use a straight-through bound.  This keeps both the
    # scalar and the gradient finite without recreating a zero-gradient clamp
    # on a PPO-active branch.  Normal float32 log-ratios (including +/-30) are
    # unaffected; float64 arithmetic also improves sequence-ratio headroom.
    source_finfo = torch.finfo(new_logprobs.dtype)
    numeric_lower = math.log(source_finfo.tiny) + 4.0
    numeric_upper = math.log(source_finfo.max) - 4.0
    zero_support = torch.isneginf(effective_log_ratio)
    safe_effective_log_ratio = torch.where(
        zero_support, torch.zeros_like(effective_log_ratio), effective_log_ratio
    )
    bounded_log_ratio = safe_effective_log_ratio.clamp(numeric_lower, numeric_upper)
    stable_finite_log_ratio = safe_effective_log_ratio + (
        bounded_log_ratio - safe_effective_log_ratio
    ).detach()
    stable_log_ratio = torch.where(
        zero_support, torch.full_like(stable_finite_log_ratio, float("-inf")),
        stable_finite_log_ratio,
    )
    surrogate = work_advantages * stable_log_ratio.exp()

    # Diagnostics are not part of the optimized scalar. Saturate impossible
    # exponent/log magnitudes (including q_new=0) to finite float64 bounds.
    diagnostic_log_ratio = torch.nan_to_num(
        work_log_ratio, nan=0.0, neginf=-700.0, posinf=700.0
    ).clamp(-700.0, 700.0)
    ratio = diagnostic_log_ratio.exp()
    approx_kl_each = (ratio - 1.0) - diagnostic_log_ratio
    clipped_each = (work_log_ratio < lower_clip_log) | (work_log_ratio > upper_clip_log)
    work_weights = weights.double()
    return PPODiagnostics(
        loss=-(work_weights * surrogate).sum(),
        approx_kl=(work_weights * approx_kl_each).sum(),
        clip_fraction=(work_weights * clipped_each.double()).sum(),
        mean_ratio=(work_weights * ratio).sum(),
        mean_log_ratio=(work_weights * diagnostic_log_ratio).sum(),
    )


def exact_forward_kl(
    policy_logits: torch.Tensor,
    reference_logits: torch.Tensor,
    action_mask: torch.Tensor,
    sample_weights: torch.Tensor,
) -> torch.Tensor:
    """Full-vocabulary KL(policy || reference) at sampled action prefixes."""

    if policy_logits.shape != reference_logits.shape:
        raise ValueError("policy/reference logits must have identical shape")
    aligned_mask = action_mask[:, 1:].to(dtype=torch.bool)
    if not bool(aligned_mask.any(dim=1).all().item()):
        raise ValueError("every sample needs a KL action position")
    policy_logp = F.log_softmax(policy_logits[:, :-1, :].float(), dim=-1)
    reference_logp = F.log_softmax(reference_logits[:, :-1, :].float(), dim=-1)
    per_position = (policy_logp.exp() * (policy_logp - reference_logp)).sum(dim=-1)
    per_sample = (per_position * aligned_mask).sum(dim=1) / aligned_mask.sum(dim=1)
    weights = sample_weights.float() / sample_weights.sum().clamp_min(1e-12)
    return (weights * per_sample).sum()


def masked_entropy(
    logits: torch.Tensor,
    action_mask: torch.Tensor,
    sample_weights: torch.Tensor,
) -> torch.Tensor:
    aligned_mask = action_mask[:, 1:].to(dtype=torch.bool)
    logp = F.log_softmax(logits[:, :-1, :].float(), dim=-1)
    entropy = -(logp.exp() * logp).sum(dim=-1)
    per_sample = (entropy * aligned_mask).sum(dim=1) / aligned_mask.sum(dim=1)
    weights = sample_weights.float() / sample_weights.sum().clamp_min(1e-12)
    return (weights * per_sample).sum()


def two_hot_distance(distance: torch.Tensor, bins: int = 64) -> torch.Tensor:
    if bins < 2:
        raise ValueError("bins must be at least 2")
    d = distance.float()
    if not bool(torch.isfinite(d).all().item()):
        raise ValueError("distance must be finite")
    if bool(((d < 1.0) | (d > float(bins))).any().item()):
        raise ValueError(f"distance must be within [1,{bins}]; overflow is rejected, never clamped")
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


def weighted_categorical_value_loss(
    value_logits: torch.Tensor,
    distances: torch.Tensor,
    sample_weights: torch.Tensor,
) -> torch.Tensor:
    if value_logits.shape[0] != distances.numel() or distances.numel() != sample_weights.numel():
        raise ValueError("value logits, distances and weights must have matching rows")
    target = two_hot_distance(distances, bins=value_logits.shape[-1])
    per_sample = -(target * F.log_softmax(value_logits.float(), dim=-1)).sum(dim=-1)
    weights = sample_weights.float() / sample_weights.sum().clamp_min(1e-12)
    return (weights * per_sample).sum()

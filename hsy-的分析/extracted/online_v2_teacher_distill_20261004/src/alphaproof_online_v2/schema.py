from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import torch


@dataclass(frozen=True)
class SearchObservation:
    """One fresh search estimate used to form a local action advantage."""

    problem_id: str
    state_id: str
    action: str
    q_value: float

    def validate(self) -> None:
        if not self.problem_id or not self.state_id or not self.action.strip():
            raise ValueError("search observation identifiers and action must be non-empty")
        if not math.isfinite(self.q_value):
            raise ValueError("q_value must be finite")


@dataclass
class RolloutSample:
    """A tactic action sampled by a single frozen behavior-policy version.

    ``action_mask`` addresses target-token positions in ``input_ids``. Position
    zero must be false because no preceding logit predicts it. ``old_logprobs``
    has the same sequence length; non-action entries are ignored.
    """

    problem_id: str
    trajectory_id: str
    state_id: str
    policy_version: str
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    action_mask: torch.Tensor
    old_logprobs: torch.Tensor
    advantage: float
    verifier_status: str = "unresolved"
    value_distance: float | None = None

    def validate(self) -> None:
        for name in ("problem_id", "trajectory_id", "state_id", "policy_version"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        tensors: Sequence[torch.Tensor] = (
            self.input_ids,
            self.attention_mask,
            self.action_mask,
            self.old_logprobs,
        )
        if any(t.ndim != 1 for t in tensors):
            raise ValueError("rollout tensors must be rank-1")
        if len({int(t.numel()) for t in tensors}) != 1:
            raise ValueError("rollout tensors must have identical lengths")
        if self.action_mask.dtype is not torch.bool:
            raise ValueError("action_mask must be bool")
        if bool(self.action_mask[0].item()):
            raise ValueError("action_mask[0] cannot be true")
        if int(self.action_mask.sum().item()) == 0:
            raise ValueError("a rollout sample must contain action tokens")
        if not torch.isfinite(self.old_logprobs[self.action_mask]).all():
            raise ValueError("old action log probabilities must be finite")
        if not math.isfinite(float(self.advantage)):
            raise ValueError("advantage must be finite")
        if self.value_distance is not None and (
            not math.isfinite(float(self.value_distance)) or self.value_distance < 0
        ):
            raise ValueError("value_distance must be finite and non-negative")


@dataclass(frozen=True)
class DistillationExample:
    problem_id: str
    state_id: str
    state: str
    action: str
    source: str
    proof_id: str

    def validate(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.problem_id,
                self.state_id,
                self.state,
                self.action,
                self.source,
                self.proof_id,
            )
        ):
            raise ValueError("distillation fields must be non-empty")

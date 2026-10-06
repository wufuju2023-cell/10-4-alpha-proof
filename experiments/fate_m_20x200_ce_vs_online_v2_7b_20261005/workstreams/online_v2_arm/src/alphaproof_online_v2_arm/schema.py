from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import torch


ALLOWED_VERIFIER_STATUSES = frozenset(
    {
        "verified_proof",
        "verified_disproof",
        "invalid_tactic",
        "unresolved",
        "timeout",
        "infrastructure_error",
    }
)


@dataclass(frozen=True)
class SearchObservation:
    """A behavior-policy action and its search estimate at one Lean state."""

    problem_id: str
    state_id: str
    action: str
    action_value: float
    sample_multiplicity: int

    def validate(self) -> None:
        if not self.problem_id or not self.state_id or not self.action.strip():
            raise ValueError("search identifiers and action must be non-empty")
        if not math.isfinite(self.action_value):
            raise ValueError("action_value must be finite")
        if type(self.sample_multiplicity) is not int or self.sample_multiplicity < 1:
            raise ValueError("sample_multiplicity must be a positive integer")


@dataclass
class RolloutSample:
    """One sampled tactic from a frozen behavior-policy wave.

    ``action_value`` is a search return/Q estimate and ``baseline_value`` is an
    action-independent state baseline. ``advantage`` is their standardized and
    clipped training coefficient; all three are retained for audit. Positive
    *local* rewards are accepted only with a strict Lean terminal receipt;
    propagated search values may still be positive on earlier verified-path
    states.
    """

    problem_id: str
    trajectory_id: str
    state_id: str
    policy_version: str
    behavior_version: str
    behavior_sha256: str
    base_version: str
    base_sha256: str
    wave_id: str
    event_id: str
    prompt_len: int
    tokenizer_sha256: str
    eos_token_id: int | None
    eos_convention: str
    search_receipt_id: str
    search_receipt_sha256: str
    state_candidate_count: int
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    action_mask: torch.Tensor
    old_logprobs: torch.Tensor
    action_value: float
    sample_multiplicity: int
    baseline_value: float
    advantage: float
    local_reward: float
    verifier_status: str
    terminal_verified: bool
    verifier_receipt_id: str
    verifier_receipt_sha256: str
    proof_path_receipt_id: str | None = None
    proof_path_receipt_sha256: str | None = None
    on_verified_solution_path: bool = False
    value_distance: float | None = None

    @property
    def old_action_logprob(self) -> float:
        return float(self.old_logprobs[self.action_mask].sum().item())

    def validate(self) -> None:
        for name in (
            "problem_id",
            "trajectory_id",
            "state_id",
            "policy_version",
            "behavior_version",
            "base_version",
            "wave_id",
            "event_id",
            "tokenizer_sha256",
            "eos_convention",
            "search_receipt_id",
            "search_receipt_sha256",
            "verifier_receipt_id",
            "verifier_receipt_sha256",
        ):
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
        if self.input_ids.dtype == torch.bool or self.input_ids.is_floating_point():
            raise ValueError("input_ids must have an integer dtype")
        attention = self.attention_mask.to(dtype=torch.long)
        if not bool(((attention == 0) | (attention == 1)).all().item()):
            raise ValueError("attention_mask must contain only 0/1")
        attended = int(attention.sum().item())
        if attended < 2 or not bool(attention[:attended].all().item()) or bool(attention[attended:].any().item()):
            raise ValueError("attention_mask must be one contiguous non-empty prefix")
        if not 1 <= self.prompt_len < attended:
            raise ValueError("prompt_len must leave an attended prompt and action")
        if bool(self.action_mask[:self.prompt_len].any().item()) or bool(self.action_mask[attended:].any().item()):
            raise ValueError("action_mask must select attended non-prompt tokens")
        selected = torch.nonzero(self.action_mask, as_tuple=False).flatten()
        if selected.numel() == 0 or not torch.equal(
            selected, torch.arange(int(selected[0]), int(selected[-1]) + 1, device=selected.device)
        ):
            raise ValueError("action_mask must be one contiguous attended action span")
        if len(self.tokenizer_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.tokenizer_sha256.lower()):
            raise ValueError("tokenizer_sha256 must be a 64-character hex SHA-256")
        for field_name in (
            "search_receipt_sha256",
            "verifier_receipt_sha256",
            "behavior_sha256",
            "base_sha256",
        ):
            value = getattr(self, field_name)
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
                raise ValueError(f"{field_name} must be a 64-character hex SHA-256")
        if self.policy_version != self.behavior_version:
            raise ValueError("policy_version must equal behavior_version")
        if self.eos_convention not in {"included_terminal", "excluded"}:
            raise ValueError("unknown eos_convention")
        action_ids = self.input_ids[self.action_mask]
        if self.eos_token_id is None:
            raise ValueError("eos_token_id must be recorded")
        eos_positions = action_ids == int(self.eos_token_id)
        if self.eos_convention == "included_terminal":
            if not bool(eos_positions[-1].item()) or int(eos_positions.sum().item()) != 1:
                raise ValueError("included_terminal requires exactly one final action EOS")
        elif bool(eos_positions.any().item()):
            raise ValueError("excluded EOS convention forbids EOS in action tokens")
        if not torch.isfinite(self.old_logprobs[self.action_mask]).all():
            raise ValueError("warped old action log probabilities must be finite")
        if self.state_candidate_count < 1:
            raise ValueError("state_candidate_count must be positive")
        for name in ("action_value", "baseline_value", "advantage", "local_reward"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        if type(self.sample_multiplicity) is not int or self.sample_multiplicity < 1:
            raise ValueError("sample_multiplicity must be a positive integer")
        if self.verifier_status not in ALLOWED_VERIFIER_STATUSES:
            raise ValueError(f"unknown verifier_status: {self.verifier_status}")
        if self.terminal_verified and self.verifier_status not in {
            "verified_proof",
            "verified_disproof",
        }:
            raise ValueError("terminal_verified requires a verified proof/disproof status")
        if self.local_reward > 0 and not self.terminal_verified:
            raise ValueError("positive reward requires strict terminal verification")
        if self.verifier_status == "infrastructure_error" and self.local_reward != 0:
            raise ValueError("infrastructure errors must have zero reward")
        if self.verifier_status in {"timeout", "unresolved"} and self.local_reward != 0:
            raise ValueError("timeout/unresolved events must have zero reward")
        if self.value_distance is not None and (
            not math.isfinite(float(self.value_distance))
            or not 1.0 <= float(self.value_distance) <= 64.0
            or not float(self.value_distance).is_integer()
        ):
            raise ValueError("value_distance must be an integer in [1,64]")
        if self.value_distance is not None and not self.on_verified_solution_path:
            raise ValueError("value target requires a strictly verified solution path")
        if self.on_verified_solution_path:
            if not self.proof_path_receipt_id or not self.proof_path_receipt_sha256:
                raise ValueError("verified-path sample requires proof-path receipt provenance")
            digest = self.proof_path_receipt_sha256
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise ValueError("proof_path_receipt_sha256 must be a 64-character hex SHA-256")
        elif self.proof_path_receipt_id is not None or self.proof_path_receipt_sha256 is not None:
            raise ValueError("off-path sample cannot carry proof-path receipt provenance")

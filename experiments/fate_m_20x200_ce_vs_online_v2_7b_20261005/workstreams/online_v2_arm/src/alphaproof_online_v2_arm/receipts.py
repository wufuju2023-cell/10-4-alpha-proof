"""Tamper-evident immutable inputs for Online-v2 sample construction.

The learner must never accept search-derived targets as loose mutable fields.
These receipts keep search estimates, strict verifier results, and selected
proof paths separate.  Each receipt is frozen and carries the SHA-256 of its
canonical payload; :mod:`builder` verifies those hashes before using it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from typing import Any, Mapping

from .schema import ALLOWED_VERIFIER_STATUSES


EOS_CONVENTIONS = frozenset({"included_terminal", "excluded", "per_candidate"})
TERMINAL_STATUSES = frozenset({"verified_proof", "verified_disproof"})
VALUE_BINS = 64


class ReceiptInvariantError(ValueError):
    """A receipt is incomplete, inconsistent, or no longer matches its hash."""


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ordered_proof_chain_sha256(
    entries: tuple[tuple[str, str, str, str, str], ...],
) -> str:
    """Hash the ordered event -> search -> verifier provenance of a proof path.

    Each entry is ``(event_id, search_receipt_id, search_receipt_sha256,
    verifier_receipt_id, verifier_receipt_sha256)``.  IDs are included as well
    as content hashes so a same-ID receipt replacement cannot be hidden behind
    an otherwise unchanged path receipt.
    """

    if not entries:
        raise ReceiptInvariantError("proof chain must be non-empty")
    event_ids: list[str] = []
    payload_entries: list[dict[str, str]] = []
    for entry in entries:
        if len(entry) != 5:
            raise ReceiptInvariantError("proof chain entry must contain five fields")
        event_id, search_id, search_sha, verifier_id, verifier_sha = entry
        for value, name in (
            (event_id, "event_id"),
            (search_id, "search_receipt_id"),
            (verifier_id, "verifier_receipt_id"),
        ):
            _require_id(value, name)
        _require_sha(search_sha, "search_receipt_sha256")
        _require_sha(verifier_sha, "verifier_receipt_sha256")
        event_ids.append(event_id)
        payload_entries.append({
            "event_id": event_id,
            "search_receipt_id": search_id,
            "search_receipt_sha256": search_sha,
            "verifier_receipt_id": verifier_id,
            "verifier_receipt_sha256": verifier_sha,
        })
    if len(set(event_ids)) != len(event_ids):
        raise ReceiptInvariantError("proof chain event_ids must be unique")
    return canonical_sha256({"ordered_event_chain": payload_entries})


def _payload(value: Any) -> dict[str, Any]:
    result = asdict(value)
    result.pop("receipt_sha256", None)
    return result


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptInvariantError(f"{name} must be non-empty")


def _require_sha(value: str, name: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ReceiptInvariantError(f"{name} must be a 64-character hex SHA-256")


@dataclass(frozen=True)
class CandidateReceipt:
    """One PPO raw action row, bound to the Lean execution that scored it."""

    event_id: str
    trajectory_id: str
    action: str
    depth: int
    parent_event_id: str | None
    input_ids: tuple[int, ...]
    attention_mask: tuple[int, ...]
    action_mask: tuple[bool, ...]
    old_logprobs: tuple[float, ...]
    action_value: float
    sample_multiplicity: int
    execution_event_id: str | None = None
    execution_action: str | None = None
    execution_action_token_ids: tuple[int, ...] = ()
    raw_sample_indices: tuple[int, ...] = ()
    tactic_token_spans: tuple[tuple[int, int], ...] = ()
    finish_reason: str | None = None
    # Optional audit-only p_old.  ``old_logprobs`` is the actual warped q_old
    # used by PPO under the frozen temperature/top-p generation policy.
    unwarped_old_logprobs: tuple[float, ...] | None = None

    def validate(self, *, prompt_token_ids: tuple[int, ...], eos_token_id: int,
                 eos_convention: str) -> None:
        for name in ("event_id", "trajectory_id", "action"):
            _require_id(getattr(self, name), name)
        if self.depth < 0:
            raise ReceiptInvariantError("candidate depth must be non-negative")
        if self.depth == 0 and self.parent_event_id is not None:
            raise ReceiptInvariantError("depth-zero candidate cannot have a parent event")
        if self.depth > 0 and not self.parent_event_id:
            raise ReceiptInvariantError("non-root candidate requires parent_event_id")
        lengths = {
            len(self.input_ids), len(self.attention_mask), len(self.action_mask),
            len(self.old_logprobs),
        }
        if len(lengths) != 1 or not self.input_ids:
            raise ReceiptInvariantError("candidate token arrays must be non-empty and equal length")
        if any(type(item) is not int for item in self.input_ids):
            raise ReceiptInvariantError("input_ids must contain integers")
        if any(item not in (0, 1) for item in self.attention_mask):
            raise ReceiptInvariantError("attention_mask must contain only 0/1")
        attended = sum(self.attention_mask)
        if attended < 2 or self.attention_mask != (1,) * attended + (0,) * (len(self.input_ids) - attended):
            raise ReceiptInvariantError("attention_mask must be one contiguous prefix")
        prompt_len = len(prompt_token_ids)
        if prompt_len < 1 or prompt_len >= attended:
            raise ReceiptInvariantError("prompt must be non-empty and precede an action")
        if self.input_ids[:prompt_len] != prompt_token_ids:
            raise ReceiptInvariantError("candidate input prefix differs from state prompt tokens")
        if any(self.action_mask[:prompt_len]) or any(self.action_mask[attended:]) or not any(self.action_mask):
            raise ReceiptInvariantError("action_mask must select attended non-prompt tokens")
        selected = [index for index, enabled in enumerate(self.action_mask) if enabled]
        if selected != list(range(selected[0], selected[-1] + 1)):
            raise ReceiptInvariantError("action_mask must be one contiguous attended action span")
        action_ids = tuple(self.input_ids[index] for index in selected)
        is_raw_row = self.execution_event_id is not None
        if is_raw_row:
            _require_id(self.execution_event_id or "", "execution_event_id")
            _require_id(self.execution_action or "", "execution_action")
            if (not self.execution_action_token_ids
                    or any(type(token) is not int or token < 0 for token in self.execution_action_token_ids)):
                raise ReceiptInvariantError("raw row requires execution tactic token IDs")
            if not self.raw_sample_indices or self.sample_multiplicity != len(self.raw_sample_indices):
                raise ReceiptInvariantError("raw row multiplicity must equal its raw sample index count")
            if (any(type(index) is not int or index < 0 for index in self.raw_sample_indices)
                    or tuple(sorted(set(self.raw_sample_indices))) != self.raw_sample_indices):
                raise ReceiptInvariantError("raw sample indices must be sorted unique nonnegative integers")
            if len(self.tactic_token_spans) != len(self.raw_sample_indices):
                raise ReceiptInvariantError("tactic spans must align with mapped raw sample indices")
            completion_len = attended - prompt_len
            for start, stop in self.tactic_token_spans:
                if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= completion_len:
                    raise ReceiptInvariantError("invalid execution tactic span in raw completion")
                if tuple(self.input_ids[prompt_len + start:prompt_len + stop]) != self.execution_action_token_ids:
                    raise ReceiptInvariantError("execution tactic token span differs from normalized Lean action")
            if self.unwarped_old_logprobs is None:
                raise ReceiptInvariantError("raw row requires audit-only unwarped p_old logprobs")
            if selected != list(range(prompt_len, attended)):
                raise ReceiptInvariantError("raw PPO action mask must cover the complete completion")
        elif eos_convention == "per_candidate":
            raise ReceiptInvariantError("per-candidate EOS states require explicit raw mapping metadata")
        row_eos = eos_convention
        if eos_convention == "per_candidate":
            if not is_raw_row or self.finish_reason not in {"stop", "length"}:
                raise ReceiptInvariantError("per-candidate EOS requires raw row finish_reason")
            row_eos = "included_terminal" if self.finish_reason == "stop" else "excluded"
        eos_count = action_ids.count(eos_token_id)
        if row_eos == "included_terminal":
            if action_ids[-1] != eos_token_id or eos_count != 1:
                raise ReceiptInvariantError("included_terminal requires exactly one final EOS")
        elif row_eos == "excluded":
            if eos_count:
                raise ReceiptInvariantError("excluded EOS convention forbids action EOS")
        else:
            raise ReceiptInvariantError(f"unknown EOS convention: {eos_convention}")
        if any(not math.isfinite(self.old_logprobs[index]) for index in selected):
            raise ReceiptInvariantError("warped action old_logprobs must be finite")
        if self.unwarped_old_logprobs is not None:
            if len(self.unwarped_old_logprobs) != len(self.input_ids):
                raise ReceiptInvariantError("unwarped audit logprobs must align to input_ids")
            if any(not math.isfinite(self.unwarped_old_logprobs[index]) for index in selected):
                raise ReceiptInvariantError("unwarped action old_logprobs must be finite")
        if not math.isfinite(self.action_value):
            raise ReceiptInvariantError("action_value must be finite")
        if type(self.sample_multiplicity) is not int or self.sample_multiplicity < 1:
            raise ReceiptInvariantError("sample_multiplicity must be a positive integer")


@dataclass(frozen=True)
class SearchStateReceipt:
    receipt_id: str
    receipt_sha256: str
    problem_id: str
    wave_id: str
    policy_version: str
    behavior_version: str
    behavior_sha256: str
    base_version: str
    base_sha256: str
    state_id: str
    tokenizer_sha256: str
    eos_token_id: int
    eos_convention: str
    prompt_token_ids: tuple[int, ...]
    candidate_set_complete: bool
    expected_candidate_count: int
    candidates: tuple[CandidateReceipt, ...]

    @classmethod
    def create(cls, **kwargs: Any) -> "SearchStateReceipt":
        value = cls(receipt_sha256="", **kwargs)
        return replace(value, receipt_sha256=canonical_sha256(_payload(value)))

    def validate(self) -> None:
        for name in (
            "receipt_id", "problem_id", "wave_id", "policy_version",
            "behavior_version", "base_version", "state_id",
        ):
            _require_id(getattr(self, name), name)
        _require_sha(self.behavior_sha256, "behavior_sha256")
        _require_sha(self.base_sha256, "base_sha256")
        if self.policy_version != self.behavior_version:
            raise ReceiptInvariantError(
                "policy_version must equal the behavior adapter version used for rollout"
            )
        _require_sha(self.tokenizer_sha256, "tokenizer_sha256")
        if self.eos_convention not in EOS_CONVENTIONS:
            raise ReceiptInvariantError("unknown eos_convention")
        if not self.candidate_set_complete:
            raise ReceiptInvariantError("partial candidate set cannot define a state baseline")
        if self.expected_candidate_count < 1 or len(self.candidates) != self.expected_candidate_count:
            raise ReceiptInvariantError("candidate count does not match completeness declaration")
        event_ids = [item.event_id for item in self.candidates]
        actions = [item.action for item in self.candidates]
        if len(set(event_ids)) != len(event_ids):
            raise ReceiptInvariantError("duplicate event_id in state candidate set")
        if len(set(actions)) != len(actions):
            raise ReceiptInvariantError("duplicate action in state candidate set")
        for item in self.candidates:
            item.validate(
                prompt_token_ids=self.prompt_token_ids,
                eos_token_id=self.eos_token_id,
                eos_convention=self.eos_convention,
            )
        if self.eos_convention == "per_candidate":
            mapped = [index for item in self.candidates for index in item.raw_sample_indices]
            if len(mapped) != len(set(mapped)) or sorted(mapped) != list(range(len(mapped))):
                raise ReceiptInvariantError("raw sample mapping must partition contiguous request indices")
        _require_sha(self.receipt_sha256, "search receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(_payload(self)):
            raise ReceiptInvariantError("search receipt content hash mismatch")


@dataclass(frozen=True)
class VerifierReceipt:
    receipt_id: str
    receipt_sha256: str
    problem_id: str
    wave_id: str
    event_id: str
    search_receipt_id: str
    search_receipt_sha256: str
    status: str
    terminal_verified: bool
    execution_event_id: str | None = None

    @classmethod
    def create(cls, **kwargs: Any) -> "VerifierReceipt":
        value = cls(receipt_sha256="", **kwargs)
        return replace(value, receipt_sha256=canonical_sha256(_payload(value)))

    def validate(self) -> None:
        for name in ("receipt_id", "problem_id", "wave_id", "event_id", "search_receipt_id"):
            _require_id(getattr(self, name), name)
        _require_sha(self.search_receipt_sha256, "search_receipt_sha256")
        if self.status not in ALLOWED_VERIFIER_STATUSES:
            raise ReceiptInvariantError(f"unknown verifier status: {self.status}")
        if self.terminal_verified != (self.status in TERMINAL_STATUSES):
            raise ReceiptInvariantError("terminal_verified must exactly match proof/disproof status")
        if self.execution_event_id is not None:
            _require_id(self.execution_event_id, "execution_event_id")
        _require_sha(self.receipt_sha256, "verifier receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(_payload(self)):
            raise ReceiptInvariantError("verifier receipt content hash mismatch")


@dataclass(frozen=True)
class ProofPathReceipt:
    receipt_id: str
    receipt_sha256: str
    problem_id: str
    wave_id: str
    outcome: str
    terminal_verifier_receipt_id: str
    terminal_verifier_receipt_sha256: str
    ordered_event_chain_sha256: str
    event_ids: tuple[str, ...]

    @classmethod
    def create(cls, **kwargs: Any) -> "ProofPathReceipt":
        value = cls(receipt_sha256="", **kwargs)
        return replace(value, receipt_sha256=canonical_sha256(_payload(value)))

    def validate(self) -> None:
        for name in ("receipt_id", "problem_id", "wave_id", "terminal_verifier_receipt_id"):
            _require_id(getattr(self, name), name)
        _require_sha(
            self.terminal_verifier_receipt_sha256,
            "terminal_verifier_receipt_sha256",
        )
        _require_sha(self.ordered_event_chain_sha256, "ordered_event_chain_sha256")
        if self.outcome not in {"proof", "disproof"}:
            raise ReceiptInvariantError("proof path outcome must be proof or disproof")
        if not self.event_ids or len(set(self.event_ids)) != len(self.event_ids):
            raise ReceiptInvariantError("proof path event_ids must be non-empty and unique")
        if len(self.event_ids) > VALUE_BINS:
            raise ReceiptInvariantError(
                f"proof path exceeds the {VALUE_BINS}-bin value horizon; "
                "long verified paths are rejected, never clamped"
            )
        _require_sha(self.receipt_sha256, "proof-path receipt_sha256")
        if self.receipt_sha256 != canonical_sha256(_payload(self)):
            raise ReceiptInvariantError("proof-path receipt content hash mismatch")

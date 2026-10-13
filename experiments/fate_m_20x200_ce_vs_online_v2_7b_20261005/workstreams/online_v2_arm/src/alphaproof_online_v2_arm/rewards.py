from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationRecord:
    status: str
    terminal_verified: bool
    receipt_id: str


def strict_local_reward(record: VerificationRecord, *, invalid_penalty: float = -0.1) -> float:
    """Map verifier outcomes to local rewards without poisoning a trajectory.

    A timeout or infrastructure failure is missing evidence, not a negative
    mathematical label. An invalid tactic may receive a bounded local penalty;
    that penalty must never be copied to earlier actions in the trajectory.
    """

    if not record.receipt_id:
        raise ValueError("verifier receipt id is required")
    if record.status in {"verified_proof", "verified_disproof"}:
        if not record.terminal_verified:
            raise ValueError("positive terminal result is missing strict verification")
        return 1.0
    if record.terminal_verified:
        raise ValueError("only proof/disproof may be terminal_verified")
    if record.status == "invalid_tactic":
        if not -1.0 <= invalid_penalty <= 0.0:
            raise ValueError("invalid_penalty must be in [-1, 0]")
        return float(invalid_penalty)
    if record.status in {"timeout", "unresolved", "infrastructure_error"}:
        return 0.0
    raise ValueError(f"unknown verifier status: {record.status}")

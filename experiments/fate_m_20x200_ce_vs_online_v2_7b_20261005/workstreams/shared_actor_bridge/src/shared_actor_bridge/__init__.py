"""Canonical-v2 boundary between REAL-Prover generation, Reap and both learners."""

from .contract import (
    ActorContractError,
    BehaviorIdentity,
    CandidateEvidence,
    GenerationParameters,
    RawSampleEvidence,
    RequestCost,
    SearchStateEvidence,
    build_unsigned_envelope,
    canonical_sha256,
    generation_receipt_payload,
    generation_receipt_sha256,
    validate_unsigned_envelope,
)
from .converters import to_ce_receipt, to_online_v2_receipts
from .io import write_immutable_envelope
from .hf_adapter import RawGeneration, generate_raw_candidates, trainable_state_sha256
from .attestation import sign_execution_attestation, verify_execution_attestation

__all__ = [
    "ActorContractError",
    "BehaviorIdentity",
    "CandidateEvidence",
    "GenerationParameters",
    "RawSampleEvidence",
    "RequestCost",
    "SearchStateEvidence",
    "build_unsigned_envelope",
    "canonical_sha256",
    "generation_receipt_payload",
    "generation_receipt_sha256",
    "validate_unsigned_envelope",
    "to_ce_receipt",
    "to_online_v2_receipts",
    "write_immutable_envelope",
    "RawGeneration",
    "generate_raw_candidates",
    "trainable_state_sha256",
    "sign_execution_attestation",
    "verify_execution_attestation",
]

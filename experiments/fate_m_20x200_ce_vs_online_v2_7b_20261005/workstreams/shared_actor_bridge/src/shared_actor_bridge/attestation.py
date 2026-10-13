"""Trusted search-execution attestation for terminal and nonterminal attempts."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .contract import (
    ActorContractError,
    actor_evidence_sha256,
    canonical_sha256,
    payload_sha256,
    validate_unsigned_envelope,
)


def _execution_bindings(receipt: Mapping[str, Any]) -> dict[str, Any]:
    evidence = []
    for state in receipt["search_states"]:
        for candidate in state["candidates"]:
            evidence.append({
                "event_id": candidate["event_id"],
                "verifier_status": candidate["verifier_status"],
                "executor_receipt_sha256": candidate["executor_receipt_sha256"],
                "execution_disposition": candidate["execution_disposition"],
                "lean_tactic_executions": candidate["lean_tactic_executions"],
            })
    return {
        "actor_envelope_sha256": actor_evidence_sha256(receipt),
        "request_sha256": receipt["request_sha256"],
        "search_states_sha256": canonical_sha256(receipt["search_states"]),
        "executor_evidence_sha256": canonical_sha256(evidence),
        "cost_sha256": canonical_sha256(receipt["cost"]),
        "outcome": receipt["outcome"],
    }


def sign_execution_attestation(receipt: Mapping[str, Any], *, tokenizer: Any,
                               attester_id: str, attester_lock_sha256: str,
                               private_key: Any) -> dict[str, Any]:
    """Sign the complete search attempt, including unsolved terminal states.

    ``private_key`` is an Ed25519 private-key object.  Key loading remains at
    the trusted executor boundary so this helper never accepts key material in
    an envelope or log.
    """
    if type(attester_id) is not str or not attester_id.strip():
        raise ActorContractError("execution attester_id must be non-empty")
    if type(attester_lock_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", attester_lock_sha256):
        raise ActorContractError("execution attester lock must be lowercase SHA-256")
    unsigned = deepcopy(dict(receipt))
    if unsigned.get("execution_attestation") not in (None, {}):
        raise ActorContractError("execution attestation already present")
    if unsigned.get("verification") not in (None, {}):
        raise ActorContractError("execution must be attested before strict proof replay")
    validate_unsigned_envelope(unsigned, tokenizer=tokenizer)
    payload = {
        "schema_version": 1,
        "attester_id": attester_id,
        "attester_lock_sha256": attester_lock_sha256,
        **_execution_bindings(unsigned),
    }
    digest = canonical_sha256(payload)
    try:
        signature = private_key.sign(bytes.fromhex(digest)).hex()
    except Exception as exc:  # pragma: no cover - wrong crypto object is deployment wiring
        raise ActorContractError("execution attester private key cannot sign") from exc
    if not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ActorContractError("execution attester returned an invalid Ed25519 signature")
    payload["attestation_receipt_sha256"] = digest
    payload["signature_hex"] = signature
    signed = deepcopy(unsigned)
    signed["execution_attestation"] = payload
    signed["receipt_sha256"] = payload_sha256(signed)
    return signed


def verify_execution_attestation(receipt: Mapping[str, Any], *, public_key_hex: str,
                                 expected_attester_id: str,
                                 expected_attester_lock_sha256: str) -> None:
    attestation = receipt.get("execution_attestation")
    if not isinstance(attestation, Mapping):
        raise ActorContractError("learner conversion requires signed search-execution evidence")
    if attestation.get("attester_id") != expected_attester_id:
        raise ActorContractError("execution attester_id differs from the frozen lock")
    if attestation.get("attester_lock_sha256") != expected_attester_lock_sha256:
        raise ActorContractError("execution attester lock differs from the frozen lock")
    expected = {
        "schema_version": 1,
        "attester_id": expected_attester_id,
        "attester_lock_sha256": expected_attester_lock_sha256,
        **_execution_bindings(receipt),
    }
    digest = canonical_sha256(expected)
    if attestation.get("attestation_receipt_sha256") != digest:
        raise ActorContractError("execution attestation payload hash mismatch")
    signature = attestation.get("signature_hex")
    if type(signature) is not str or not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ActorContractError("execution attestation lacks a valid Ed25519 signature")
    if type(public_key_hex) is not str or not re.fullmatch(r"[0-9a-f]{64}", public_key_hex):
        raise ActorContractError("execution public key must be 32-byte lowercase hex")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature), bytes.fromhex(digest))
    except (ValueError, InvalidSignature) as exc:
        raise ActorContractError("execution attestation Ed25519 signature check failed") from exc
    if dict(attestation) != {**expected, "attestation_receipt_sha256": digest, "signature_hex": signature}:
        raise ActorContractError("execution attestation contains unrecognized claims")

"""Canonical actor receipt validation and trusted verifier signing.

The actor owns prompts, token IDs and the selected state/action path.  The
verifier never invents those fields: it validates their hashes and chain, then
adds a detached Ed25519 signature only after an independent Lean replay.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VALUE_BINS = 64


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def object_hash(value: object, excluded_key: str | None = None) -> str:
    if excluded_key is not None:
        if not isinstance(value, dict):
            raise TypeError("excluded_key requires an object")
        value = {key: item for key, item in value.items() if key != excluded_key}
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_verifier_lock(
    path: Path,
    *,
    expected_sha256: str,
    runtime_receipt_sha256: str,
    lake_manifest_sha256: str,
    lake_executable_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if sha256_file(path) != expected_sha256:
        raise ValueError("trusted verifier lock hash mismatch")
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("trusted verifier lock is unreadable or invalid JSON") from exc
    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        raise ValueError("unsupported trusted verifier lock schema")
    exact = {
        "lean_toolchain": "leanprover/lean4:v4.28.0",
        "runtime_receipt_sha256": runtime_receipt_sha256,
        "lake_manifest_sha256": lake_manifest_sha256,
        "executor_binary_sha256": lake_executable_sha256,
        "tactic_timeout_seconds": timeout_seconds,
    }
    for field, expected in exact.items():
        if lock.get(field) != expected:
            raise ValueError(f"trusted verifier lock {field} mismatch")
    for field in ("verifier_id", "lean_version", "lean_git_hash", "mathlib_revision",
                  "executor_source_sha256"):
        value = lock.get(field)
        if type(value) is not str or not value:
            raise ValueError(f"trusted verifier lock lacks {field}")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["mathlib_revision"]):
        raise ValueError("trusted verifier lock mathlib_revision is not a git commit")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["lean_git_hash"]):
        raise ValueError("trusted verifier lock lean_git_hash is not a git commit")
    if not SHA256_RE.fullmatch(lock["executor_source_sha256"]):
        raise ValueError("trusted verifier lock executor_source_sha256 is not SHA-256")
    public_key = lock.get("ed25519_public_key_hex")
    if type(public_key) is not str or not re.fullmatch(r"[0-9a-f]{64}", public_key):
        raise ValueError("trusted verifier lock lacks a 32-byte Ed25519 public key")
    command = lock.get("kernel_command")
    required_command = ["{lake}", "env", "lean", "--json", "-E", "hasSorry", "{theorem}"]
    if command != required_command:
        raise ValueError("trusted verifier lock kernel_command is not the hardened command")
    forbidden = lock.get("forbidden_tokens")
    if not isinstance(forbidden, list) or not {"sorry", "admit", "holes"}.issubset(set(forbidden)):
        raise ValueError("trusted verifier lock does not forbid sorry/admit/holes")
    return lock


def _hash_string(value: object, label: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def validate_unsigned_actor_receipt(
    receipt: dict[str, Any],
    *,
    problem_id: str,
    statement_sha256: str,
    require_unsigned: bool = True,
) -> tuple[str, str, str]:
    """Validate actor-owned bindings and return request/path/final-state hashes."""
    if receipt.get("schema_version") != 2:
        raise ValueError("actor receipt must use canonical schema_version=2")
    if receipt.get("problem_id") != problem_id or receipt.get("outcome") != "proof":
        raise ValueError("actor receipt problem/outcome mismatch")
    if receipt.get("statement_sha256") != statement_sha256:
        raise ValueError("actor receipt statement hash mismatch")
    if require_unsigned and receipt.get("verification") not in (None, {}):
        raise ValueError("unsigned actor receipt must not contain verification claims")
    for field in ("receipt_id", "attempt_id"):
        if type(receipt.get(field)) is not str or not receipt[field]:
            raise ValueError(f"actor receipt lacks {field}")
    for field in ("actor_config_sha256", "budget_config_sha256", "tokenizer_lock_sha256"):
        _hash_string(receipt.get(field), f"actor receipt {field}")
    cost = receipt.get("cost")
    if (not isinstance(cost, dict)
            or type(cost.get("generated_tokens")) is not int or cost["generated_tokens"] < 0
            or type(cost.get("lean_tactic_executions")) is not int
            or cost["lean_tactic_executions"] < 0):
        raise ValueError("actor receipt cost counters must be nonnegative integers")
    request = receipt.get("request")
    if not isinstance(request, dict):
        raise ValueError("actor receipt lacks request")
    if request.get("problem_id") != problem_id or request.get("statement_sha256") != statement_sha256:
        raise ValueError("actor request is not bound to problem/statement")
    for field in ("actor_config_sha256", "budget_config_sha256"):
        if request.get(field) != receipt[field]:
            raise ValueError(f"actor request {field} mismatch")
    initial = _hash_string(request.get("initial_state_sha256"), "initial_state_sha256")
    request_hash = object_hash(request)
    if receipt.get("request_sha256") != request_hash:
        raise ValueError("actor request_sha256 mismatch")
    path = receipt.get("selected_path")
    if not isinstance(path, list) or not path:
        raise ValueError("proof actor receipt has no selected_path")
    path_length = len(path)
    if path_length > VALUE_BINS:
        raise ValueError(
            f"selected path has {path_length} tactics, exceeding the {VALUE_BINS}-bin "
            "value horizon; long paths are rejected, never clamped"
        )
    previous = initial
    for index, step in enumerate(path):
        if not isinstance(step, dict) or type(step.get("step_index")) is not int or step["step_index"] != index:
            raise ValueError(f"selected path has malformed step {index}")
        before = _hash_string(step.get("state_before_sha256"), f"step {index} state_before")
        after = _hash_string(step.get("state_after_sha256"), f"step {index} state_after")
        if before != previous:
            raise ValueError(f"selected path state chain breaks at step {index}")
        previous = after
        if type(step.get("prompt")) is not str or not step["prompt"].strip():
            raise ValueError(f"selected path step {index} lacks prompt")
        if type(step.get("action")) is not str or not step["action"].strip():
            raise ValueError(f"selected path step {index} lacks action")
        for field in ("prompt_token_ids", "raw_completion_token_ids"):
            ids = step.get(field)
            if (not isinstance(ids, list) or not ids or
                    any(type(token) is not int or token < 0 for token in ids)):
                raise ValueError(f"selected path step {index} has invalid {field}")
        span = step.get("tactic_token_span")
        raw_ids = step["raw_completion_token_ids"]
        if (not isinstance(span, list) or len(span) != 2 or
                any(type(item) is not int for item in span) or
                not 0 <= span[0] < span[1] <= len(raw_ids)):
            raise ValueError(f"selected path step {index} has invalid tactic_token_span")
        # Value supervision is verifier-derived, not an actor assertion.  The
        # actor field is retained only as a signed consistency field so older
        # consumers can read the canonical receipt without another sidecar.
        expected_value_target = -(path_length - index)
        reported_value_target = step.get("value_target")
        if (type(reported_value_target) not in {int, float}
                or reported_value_target != expected_value_target):
            raise ValueError(
                f"selected path step {index} value_target mismatch: actor reported "
                f"{reported_value_target!r}, verified path derives {expected_value_target}"
            )
    return request_hash, object_hash(path), previous


def sign_actor_receipt(
    unsigned: dict[str, Any],
    *,
    verifier_lock: dict[str, Any],
    verifier_lock_sha256: str,
    request_sha256: str,
    statement_sha256: str,
    selected_path_sha256: str,
    initial_state_sha256: str,
    final_state_sha256: str,
    kernel_receipt_sha256: str,
    private_key_path: Path,
) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # Keep this guard inside the signing primitive as well as the replay entry
    # point: no caller can bypass path/value derivation and obtain a trusted
    # signature for an actor-selected or mislabelled value target.
    derived_request_hash, derived_path_hash, derived_final_state = validate_unsigned_actor_receipt(
        unsigned,
        problem_id=str(unsigned.get("problem_id", "")),
        statement_sha256=statement_sha256,
        require_unsigned=True,
    )
    derived_initial_state = str(unsigned["request"]["initial_state_sha256"])
    expected_bindings = {
        "request_sha256": (request_sha256, derived_request_hash),
        "selected_path_sha256": (selected_path_sha256, derived_path_hash),
        "initial_state_sha256": (initial_state_sha256, derived_initial_state),
        "final_state_sha256": (final_state_sha256, derived_final_state),
    }
    for label, (provided, derived) in expected_bindings.items():
        if provided != derived:
            raise ValueError(f"signer {label} does not match the canonical actor receipt")

    key_bytes = private_key_path.read_bytes()
    try:
        if len(key_bytes) == 32:
            private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
        else:
            loaded = serialization.load_pem_private_key(key_bytes, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("private key is not Ed25519")
            private_key = loaded
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("unable to load Ed25519 verifier private key") from exc
    public_hex = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    if public_hex != verifier_lock["ed25519_public_key_hex"]:
        raise ValueError("verifier private key does not match trusted lock public key")
    verification = {
        "verifier_id": verifier_lock["verifier_id"],
        "verifier_lock_sha256": verifier_lock_sha256,
        "request_sha256": request_sha256,
        "statement_sha256": statement_sha256,
        "selected_path_sha256": selected_path_sha256,
        "initial_state_sha256": initial_state_sha256,
        "final_state_sha256": final_state_sha256,
        "result": "verified",
        "kernel_exit_code": 0,
        "kernel_receipt_sha256": kernel_receipt_sha256,
        "actor_config_sha256": unsigned["actor_config_sha256"],
        "budget_config_sha256": unsigned["budget_config_sha256"],
        "tokenizer_lock_sha256": unsigned["tokenizer_lock_sha256"],
        "cost_sha256": object_hash(unsigned["cost"]),
        "actor_envelope_sha256": object_hash({
            key: value for key, value in unsigned.items()
            if key not in {"verification", "receipt_sha256"}
        }),
    }
    verification_hash = object_hash(verification)
    verification["verification_receipt_sha256"] = verification_hash
    verification["signature_hex"] = private_key.sign(bytes.fromhex(verification_hash)).hex()
    signed = dict(unsigned)
    signed["verification"] = verification
    signed.pop("receipt_sha256", None)
    signed["receipt_sha256"] = object_hash(signed)
    return signed


def verify_signature(verification: dict[str, Any], public_key_hex: str) -> tuple[bool, str]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    payload = {key: value for key, value in verification.items()
               if key not in {"verification_receipt_sha256", "signature_hex"}}
    payload_hash = object_hash(payload)
    if verification.get("verification_receipt_sha256") != payload_hash:
        return False, "verification receipt hash mismatch"
    signature = verification.get("signature_hex")
    if type(signature) is not str or not re.fullmatch(r"[0-9a-f]{128}", signature):
        return False, "verification lacks an Ed25519 signature"
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature), bytes.fromhex(payload_hash))
    except (ValueError, InvalidSignature):
        return False, "trusted verifier signature check failed"
    return True, "trusted verifier signature is valid"

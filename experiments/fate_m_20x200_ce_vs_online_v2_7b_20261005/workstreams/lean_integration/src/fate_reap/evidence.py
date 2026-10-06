"""Fail-closed admission of Lean, replay, and model-service evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .canonical_receipt import object_hash, validate_unsigned_actor_receipt, verify_signature
from .service_receipts import ServiceAudit, audit_service_receipts


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Evidence:
    session_id: str
    classification: str
    strict_solved: bool
    reward: float | None
    negative_reward_eligible: bool
    status: str
    reason: str
    service_audit: dict[str, Any] | None = None
    strict_replay_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationAudit:
    valid: bool
    policy_events: int
    value_events: int
    nonempty_policy_events: int
    reason: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _raw_openai_response(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Accept the actual Reap Option String, or an already-decoded fixture once."""
    if value is None:
        return None, "null tactic_gen result"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            return None, f"malformed raw tactic_gen response: {exc.msg}"
    if not isinstance(value, dict):
        return None, "tactic_gen result must be a raw JSON string or object"
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, "tactic_gen response must contain nonempty choices"
    for choice in choices:
        if not isinstance(choice, dict):
            return None, "tactic_gen choice must be an object"
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return None, "tactic_gen choice must contain string message.content"
    return value, None


def audit_wall_clock(path: Path) -> GenerationAudit:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return GenerationAudit(False, 0, 0, 0, "missing wall_clock.jsonl")
    policy_events = value_events = nonempty = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return GenerationAudit(False, policy_events, value_events, nonempty, f"malformed wall-clock JSON at line {number}")
        if not isinstance(record, dict):
            return GenerationAudit(False, policy_events, value_events, nonempty, f"wall-clock record {number} is not an object")
        name = record.get("name")
        if name == "tactic_gen":
            policy_events += 1
            extra = record.get("extra")
            if not isinstance(extra, dict):
                return GenerationAudit(False, policy_events, value_events, nonempty, f"tactic_gen line {number} has invalid extra")
            response, error = _raw_openai_response(extra.get("result"))
            if error is not None:
                return GenerationAudit(False, policy_events, value_events, nonempty, f"line {number}: {error}")
            assert response is not None
            if not any(str(choice["message"]["content"]).strip() for choice in response["choices"]):
                return GenerationAudit(False, policy_events, value_events, nonempty, f"line {number}: all choices are empty")
            nonempty += 1
        elif name == "value":
            value_events += 1
    if policy_events == 0:
        return GenerationAudit(False, 0, value_events, 0, "no successful tactic_gen event")
    return GenerationAudit(True, policy_events, value_events, nonempty, "wall-clock generation schema is valid")


def _strict_result(result: dict[str, Any], session_id: str) -> tuple[str | None, bool | None, str | None, str]:
    if result.get("schema_version") != "reap.training.result.v1":
        return None, None, None, "unexpected result schema"
    if type(result.get("session_id")) is not str or result["session_id"] != session_id:
        return None, None, None, "result session mismatch or invalid type"
    if type(result.get("solved")) is not bool:
        return None, None, None, "result solved must be a JSON boolean"
    if type(result.get("status")) is not str:
        return None, None, None, "result status must be a string"
    proof = result.get("proof_script")
    if proof is not None and type(proof) is not str:
        return None, None, None, "proof_script must be string or null"
    error = result.get("error")
    if error is not None and type(error) is not str:
        return None, None, None, "error must be string or null"
    elapsed = result.get("elapsed_ns")
    if elapsed is not None and (type(elapsed) is not int or elapsed < 0):
        return None, None, None, "elapsed_ns must be a nonnegative integer or null"
    return result["status"], result["solved"], proof, "valid"


def validate_strict_replay(
    path: Path,
    *,
    session_id: str,
    source_sha256: str,
    generated_sha256: str,
    problems_sha256: str,
    result_sha256: str,
    raw_tree_sha256: str,
    service_receipts_sha256: str,
    proof_sha256: str,
    model_sha256: str,
    runtime_receipt_sha256: str,
    course_manifest_sha256: str,
    strict_lake_sha256: str,
    verifier_id: str,
    verifier_lock_sha256: str,
    verifier_public_key_hex: str,
) -> tuple[bool, str, str | None]:
    receipt = _read_json(path)
    if receipt is None:
        return False, "missing or invalid strict replay receipt", None
    if receipt.get("receipt_sha256") != object_hash(receipt, "receipt_sha256"):
        return False, "canonical receipt_sha256 mismatch", None
    try:
        request_hash, selected_path_hash, final_state_hash = validate_unsigned_actor_receipt(
            receipt, problem_id=session_id, statement_sha256=source_sha256,
            require_unsigned=False,
        )
    except ValueError as exc:
        return False, f"canonical actor receipt rejected: {exc}", None
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        return False, "canonical receipt lacks verification", None
    signature_ok, signature_reason = verify_signature(verification, verifier_public_key_hex)
    if not signature_ok:
        return False, signature_reason, None
    required_verification = {
        "verifier_id": verifier_id,
        "verifier_lock_sha256": verifier_lock_sha256,
        "request_sha256": request_hash,
        "statement_sha256": source_sha256,
        "selected_path_sha256": selected_path_hash,
        "initial_state_sha256": receipt["request"]["initial_state_sha256"],
        "final_state_sha256": final_state_hash,
        "result": "verified",
        "kernel_exit_code": 0,
        "actor_config_sha256": receipt["actor_config_sha256"],
        "budget_config_sha256": receipt["budget_config_sha256"],
        "tokenizer_lock_sha256": receipt["tokenizer_lock_sha256"],
        "cost_sha256": object_hash(receipt["cost"]),
        "actor_envelope_sha256": object_hash({
            key: value for key, value in receipt.items()
            if key not in {"verification", "receipt_sha256"}
        }),
    }
    for field, wanted in required_verification.items():
        if verification.get(field) != wanted:
            return False, f"canonical verification {field} mismatch", None
    kernel_path = path.parent / "kernel_receipt.json"
    kernel = _read_json(kernel_path)
    if kernel is None:
        return False, "missing or invalid kernel receipt", None
    if verification.get("kernel_receipt_sha256") != sha256_file(kernel_path):
        return False, "canonical verification kernel receipt hash mismatch", None
    if kernel.get("kernel_receipt_sha256") != object_hash(kernel, "kernel_receipt_sha256"):
        return False, "kernel receipt self-hash mismatch", None
    expected_strings = {
        "schema_version": "fate.strict_replay.v2", "session_id": session_id,
        "source_id": session_id, "source_sha256": source_sha256,
        "generated_sha256": generated_sha256, "problems_sha256": problems_sha256,
        "result_sha256": result_sha256, "raw_tree_sha256": raw_tree_sha256,
        "service_receipts_sha256": service_receipts_sha256,
        "proof_sha256": proof_sha256, "model_sha256": model_sha256,
        "runtime_receipt_sha256": runtime_receipt_sha256,
        "lake_manifest_sha256": course_manifest_sha256,
        "lake_executable_sha256": strict_lake_sha256,
        "verifier_id": verifier_id, "verifier_lock_sha256": verifier_lock_sha256,
        "request_sha256": request_hash, "statement_sha256": source_sha256,
        "selected_path_sha256": selected_path_hash,
        "initial_state_sha256": receipt["request"]["initial_state_sha256"],
        "final_state_sha256": final_state_hash,
    }
    for field, wanted in expected_strings.items():
        if type(kernel.get(field)) is not str or kernel[field] != wanted:
            return False, f"strict replay {field} mismatch", None
    if type(kernel.get("strict_solved")) is not bool or not kernel["strict_solved"]:
        return False, "strict replay did not solve", None
    if type(kernel.get("returncode")) is not int or kernel["returncode"] != 0:
        return False, "strict replay process did not exit zero", None
    if type(kernel.get("timed_out")) is not bool or kernel["timed_out"]:
        return False, "strict replay timed out", None
    if kernel.get("forbidden_tokens") != []:
        return False, "strict replay found forbidden proof tokens", None
    diagnostics = kernel.get("diagnostics")
    if not isinstance(diagnostics, list):
        return False, "strict replay diagnostics must be a list", None
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            return False, "strict replay diagnostic is not an object", None
        if diagnostic.get("kind") == "hasSorry" or diagnostic.get("severity") == "error":
            return False, "strict replay contains a forbidden/error diagnostic", None
    for field in ("proof_sha256", "theorem_sha256", "stdout_sha256", "stderr_sha256",
                  "lake_manifest_sha256", "lake_executable_sha256", "runtime_receipt_sha256",
                  "model_sha256"):
        if type(kernel.get(field)) is not str or not SHA256_RE.fullmatch(kernel[field]):
            return False, f"strict replay {field} is not a SHA-256", None
    if type(kernel.get("lean_version")) is not str or "version 4.28.0" not in kernel["lean_version"]:
        return False, "strict replay did not use Lean 4.28.0", None
    if type(kernel.get("lean_githash")) is not str or not re.fullmatch(r"[0-9a-f]{40}", kernel["lean_githash"]):
        return False, "strict replay Lean git hash is invalid", None
    if kernel.get("lean_toolchain") != "leanprover/lean4:v4.28.0":
        return False, "strict replay toolchain mismatch", None
    command = kernel.get("command")
    if (not isinstance(command, list) or any(type(item) is not str for item in command)
            or "--json" not in command or "hasSorry" not in command):
        return False, "strict replay command is not hardened", None
    if not isinstance(kernel.get("timeout_seconds"), (int, float)) or kernel["timeout_seconds"] <= 0:
        return False, "strict replay timeout is invalid", None
    bound_files = {
        path.parent / f"{session_id}.lean": kernel["theorem_sha256"],
        path.parent / "stdout.log": kernel["stdout_sha256"],
        path.parent / "stderr.log": kernel["stderr_sha256"],
    }
    for bound_path, wanted_hash in bound_files.items():
        if not bound_path.is_file() or sha256_file(bound_path) != wanted_hash:
            return False, f"strict replay bound file mismatch: {bound_path.name}", None
    lake_path = kernel.get("lake_executable")
    if type(lake_path) is not str or not Path(lake_path).is_file() or sha256_file(Path(lake_path)) != strict_lake_sha256:
        return False, "strict replay lake executable no longer matches pin", None
    return True, "trusted signed canonical receipt matches actor path and strict Lean replay", sha256_file(path)


def classify_evidence(
    session_id: str,
    session_dir: Path,
    *,
    returncode: int,
    timed_out: bool,
    source_sha256: str,
    generated_sha256: str,
    problems_sha256: str,
    tree_id: str,
    model_sha256: str,
    runtime_receipt_sha256: str,
    course_manifest_sha256: str,
    strict_lake_sha256: str,
    verifier_id: str,
    verifier_lock_sha256: str,
    verifier_public_key_hex: str,
    strict_replay_receipt: Path | None = None,
) -> Evidence:
    if timed_out:
        return Evidence(session_id, "infrastructure_error", False, None, False, "timeout", "process deadline exceeded")
    result_path = session_dir / "result.json"
    result = _read_json(result_path)
    if result is None:
        return Evidence(session_id, "infrastructure_error", False, None, False, "missing_result", "missing or invalid result.json")
    status, solved, proof, result_reason = _strict_result(result, session_id)
    if result_reason != "valid":
        return Evidence(session_id, "infrastructure_error", False, None, False, "invalid_result", result_reason)
    assert status is not None and solved is not None
    wall = audit_wall_clock(session_dir / "wall_clock.jsonl")
    service: ServiceAudit = audit_service_receipts(
        session_dir / "service_requests.jsonl", session_id=session_id, tree_id=tree_id,
        expected_model_sha256=model_sha256,
        expected_policy_requests=wall.policy_events, expected_value_requests=wall.value_events,
    )
    service_dict = service.to_dict()
    if not wall.valid:
        return Evidence(session_id, "indeterminate", False, None, False, status, wall.reason, service_dict)
    if not service.valid:
        return Evidence(session_id, "indeterminate", False, None, False, status, service.reason, service_dict)
    raw_tree = session_dir / "raw_tree.json"
    if not raw_tree.is_file():
        return Evidence(session_id, "infrastructure_error", False, None, False, status, "missing raw_tree.json", service_dict)
    service_path = session_dir / "service_requests.jsonl"
    if solved:
        if status != "solved" or returncode != 0 or not isinstance(proof, str) or not proof.strip():
            return Evidence(session_id, "infrastructure_error", False, None, False, status, "solved receipt is inconsistent", service_dict)
        if strict_replay_receipt is None:
            return Evidence(session_id, "infrastructure_error", False, None, False, status, "strict replay receipt is mandatory", service_dict)
        replay_ok, reason, replay_hash = validate_strict_replay(
            strict_replay_receipt, session_id=session_id, source_sha256=source_sha256,
            generated_sha256=generated_sha256, problems_sha256=problems_sha256,
            result_sha256=sha256_file(result_path), raw_tree_sha256=sha256_file(raw_tree),
            service_receipts_sha256=sha256_file(service_path),
            proof_sha256=hashlib.sha256(proof.encode("utf-8")).hexdigest(),
            model_sha256=model_sha256, runtime_receipt_sha256=runtime_receipt_sha256,
            course_manifest_sha256=course_manifest_sha256,
            strict_lake_sha256=strict_lake_sha256,
            verifier_id=verifier_id, verifier_lock_sha256=verifier_lock_sha256,
            verifier_public_key_hex=verifier_public_key_hex,
        )
        if not replay_ok:
            return Evidence(session_id, "verification_rejected", False, None, False, status, reason, service_dict, replay_hash)
        return Evidence(session_id, "strict_success", True, 1.0, False, status, reason, service_dict, replay_hash)
    if status == "exhausted":
        if returncode == 0:
            return Evidence(session_id, "infrastructure_error", False, None, False, status, "exhausted Lean process unexpectedly exited zero", service_dict)
        return Evidence(session_id, "model_unsolved", False, 0.0, True, status, "search exhausted with a complete all-success service chain", service_dict)
    if status in {"final_check_failed", "replay_check_failed"}:
        return Evidence(session_id, "verification_rejected", False, None, False, status, "candidate failed proof verification; retained without learning reward", service_dict)
    return Evidence(session_id, "infrastructure_error", False, None, False, status, str(result.get("error") or "unexpected Lean failure"), service_dict)

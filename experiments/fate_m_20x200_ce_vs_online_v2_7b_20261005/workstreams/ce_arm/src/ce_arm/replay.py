"""Strict actor/verifier receipt admission and atomic CE replay publication."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value: object, excluded_key: str | None = None) -> str:
    if excluded_key is not None:
        if not isinstance(value, dict):
            raise TypeError("excluded_key requires an object")
        value = {key: item for key, item in value.items() if key != excluded_key}
    return sha256_bytes(canonical_bytes(value))


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class ReplayPolicy:
    course_manifest_path: Path
    course_manifest_sha256: str
    ce_config_sha256: str
    max_wave: int
    actor_config_sha256: str
    budget_config_sha256: str
    tokenizer_lock_sha256: str
    trusted_verifier_id: str
    trusted_verifier_lock_sha256: str
    trusted_verifier_public_key_hex: str
    max_attempts_per_problem: int
    max_generated_tokens_per_problem: int
    max_lean_tactic_executions_per_problem: int
    value_bins: int = 64
    distance_overflow_policy: str = "reject"

    def __post_init__(self) -> None:
        if not 1 <= self.max_wave <= 200:
            raise ValueError("max_wave must be in the source variant range 1..200")
        if self.value_bins != 64:
            raise ValueError("formal CE replay requires exactly 64 value bins")
        if self.distance_overflow_policy != "reject":
            raise ValueError("formal CE replay requires distance_overflow_policy='reject'")
        for name in (
            "max_attempts_per_problem", "max_generated_tokens_per_problem",
            "max_lean_tactic_executions_per_problem",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"shared actor budget {name} must be a positive integer")
        if len(self.trusted_verifier_public_key_hex) != 64:
            raise ValueError("trusted verifier lock must contain a 32-byte Ed25519 public key")


@dataclass
class ReplayStats:
    receipts: int = 0
    verified_proofs: int = 0
    transitions: int = 0
    skipped_unsolved: int = 0
    skipped_timeout: int = 0
    skipped_infra_error: int = 0
    generated_tokens: int = 0
    lean_tactic_executions: int = 0
    problems: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        data = vars(self).copy()
        data["problems"] = len(self.problems)
        return data


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc


def iter_receipts(path: str | Path) -> Iterator[dict]:
    """Read either the joiner's single JSON object or an accumulated JSONL file.

    The real signed join deliberately publishes ``ce-receipt.json`` as an
    immutable, pretty-printed JSON object.  Larger waves may concatenate the
    same canonical objects as JSONL.  Accepting both representations avoids a
    lossy/rehashed staging copy while preserving the exact source-file hash in
    the replay manifest.
    """
    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        yield from iter_jsonl(source)
        return
    if isinstance(value, dict):
        yield value
        return
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        yield from value
        return
    raise ValueError(f"{source}: receipt input must be one JSON object, an object array, or JSONL")


def _load_course_index(policy: ReplayPolicy) -> tuple[dict[str, dict], str]:
    manifest_path = policy.course_manifest_path.resolve()
    actual_manifest_hash = sha256_file(manifest_path)
    if actual_manifest_hash != policy.course_manifest_sha256:
        raise ValueError(
            f"course manifest hash mismatch: expected {policy.course_manifest_sha256}, got {actual_manifest_hash}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    adaptation_range = manifest.get("adaptation_range")
    if (not isinstance(adaptation_range, list) or len(adaptation_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int)
                   for value in adaptation_range)
            or adaptation_range[0] != 1 or policy.max_wave > adaptation_range[1]):
        raise ValueError("max_wave is outside the frozen course adaptation range")
    entry = manifest.get("files", {}).get("course_index.jsonl")
    if not isinstance(entry, dict):
        raise ValueError("course manifest has no course_index.jsonl entry")
    index_path = manifest_path.parent / "course_index.jsonl"
    index_hash = sha256_file(index_path)
    if index_hash != entry.get("sha256"):
        raise ValueError("course_index.jsonl does not match the frozen course manifest")
    index: dict[str, dict] = {}
    for row in iter_jsonl(index_path):
        problem_id = str(row.get("id", ""))
        if not problem_id or problem_id in index:
            raise ValueError(f"invalid/duplicate course index id: {problem_id!r}")
        index[problem_id] = row
    expected_rows = manifest.get("selected_rows", 4000)
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows <= 0:
        raise ValueError("course manifest has invalid selected_rows")
    if len(index) != expected_rows:
        raise ValueError(f"course index has {len(index)} rows, expected {expected_rows}")
    return index, index_hash


def _validate_ids(ids: object, *, vocabulary_size: int, label: str) -> list[int]:
    if not isinstance(ids, list) or not ids:
        raise ValueError(f"{label} must be a non-empty token-id list")
    output = []
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} contains a non-integer token id")
        if not 0 <= value < vocabulary_size:
            raise ValueError(f"{label} token id {value} is outside vocabulary size {vocabulary_size}")
        output.append(value)
    return output


def _decode(tokenizer, ids: list[int]) -> str:
    return tokenizer.decode(ids, skip_special_tokens=False, clean_up_tokenization_spaces=False)


def _normal_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _verification_payload_hash(verification: dict) -> str:
    payload = {key: value for key, value in verification.items()
               if key not in {"verification_receipt_sha256", "signature_hex"}}
    return object_hash(payload)


def _verify_verifier_signature(verification: dict, policy: ReplayPolicy) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    payload_hash = _verification_payload_hash(verification)
    if verification.get("verification_receipt_sha256") != payload_hash:
        raise ValueError("verifier receipt hash mismatch")
    signature = verification.get("signature_hex")
    if not isinstance(signature, str) or len(signature) != 128:
        raise ValueError("verifier receipt lacks a valid Ed25519 signature")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(policy.trusted_verifier_public_key_hex)
        )
        public_key.verify(bytes.fromhex(signature), bytes.fromhex(payload_hash))
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("trusted verifier signature check failed") from exc


def _validate_receipt(receipt: dict, policy: ReplayPolicy, course: dict[str, dict], tokenizer) -> list[dict]:
    if int(receipt.get("schema_version", 0)) != 2:
        raise ValueError("formal replay requires canonical_search_receipt schema_version=2")
    problem_id = str(receipt.get("problem_id", ""))
    course_row = course.get(problem_id)
    if course_row is None:
        raise ValueError(f"{problem_id}: problem is absent from the frozen course")
    if course_row.get("split") not in {"adaptation", "train"}:
        raise ValueError(f"{problem_id}: held-out problem is forbidden in replay")
    variant = int(course_row["variant_index"])
    if variant > policy.max_wave:
        raise ValueError(f"{problem_id}: future wave {variant} exceeds current max_wave={policy.max_wave}")
    if receipt.get("statement_sha256") != course_row.get("statement_sha256"):
        raise ValueError(f"{problem_id}: statement hash does not match the frozen course")
    if receipt.get("actor_config_sha256") != policy.actor_config_sha256:
        raise ValueError(f"{problem_id}: shared actor config hash mismatch")
    if receipt.get("budget_config_sha256") != policy.budget_config_sha256:
        raise ValueError(f"{problem_id}: shared budget config hash mismatch")
    if receipt.get("tokenizer_lock_sha256") != policy.tokenizer_lock_sha256:
        raise ValueError(f"{problem_id}: tokenizer lock hash mismatch")
    if receipt.get("receipt_sha256") != object_hash(receipt, "receipt_sha256"):
        raise ValueError(f"{problem_id}: receipt_sha256 mismatch")

    request = receipt.get("request")
    if not isinstance(request, dict):
        raise ValueError(f"{problem_id}: missing request object")
    request_hash = object_hash(request)
    if receipt.get("request_sha256") != request_hash:
        raise ValueError(f"{problem_id}: request_sha256 mismatch")
    expected_request = {
        "problem_id": problem_id,
        "statement_sha256": course_row["statement_sha256"],
        "wave_index": variant,
        "actor_config_sha256": policy.actor_config_sha256,
        "budget_config_sha256": policy.budget_config_sha256,
    }
    for key, expected in expected_request.items():
        if request.get(key) != expected:
            raise ValueError(f"{problem_id}: request {key} is not bound to the frozen run")
    initial_state_hash = str(request.get("initial_state_sha256", ""))
    if len(initial_state_hash) != 64:
        raise ValueError(f"{problem_id}: request lacks initial_state_sha256")

    cost = receipt.get("cost")
    if not isinstance(cost, dict):
        raise ValueError(f"{problem_id}: missing actor cost counters")
    generated = cost.get("generated_tokens", -1)
    lean_calls = cost.get("lean_tactic_executions", -1)
    if (isinstance(generated, bool) or not isinstance(generated, int) or
            isinstance(lean_calls, bool) or not isinstance(lean_calls, int)):
        raise ValueError(f"{problem_id}: actor cost counters must be integers")
    if not 0 <= generated <= policy.max_generated_tokens_per_problem:
        raise ValueError(f"{problem_id}: generated-token budget exceeded or invalid")
    if not 0 <= lean_calls <= policy.max_lean_tactic_executions_per_problem:
        raise ValueError(f"{problem_id}: Lean-call budget exceeded or invalid")
    if receipt.get("outcome") != "proof":
        return []

    path = receipt.get("selected_path")
    if not isinstance(path, list) or not path:
        raise ValueError(f"{problem_id}: proof receipt has no selected path")
    path_hash = object_hash(path)
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        raise ValueError(f"{problem_id}: proof lacks a verifier receipt")
    try:
        _verify_verifier_signature(verification, policy)
    except ValueError as exc:
        raise ValueError(f"{problem_id}: {exc}") from exc
    required_verification = {
        "verifier_id": policy.trusted_verifier_id,
        "verifier_lock_sha256": policy.trusted_verifier_lock_sha256,
        "actor_config_sha256": policy.actor_config_sha256,
        "budget_config_sha256": policy.budget_config_sha256,
        "tokenizer_lock_sha256": policy.tokenizer_lock_sha256,
        "request_sha256": request_hash,
        "statement_sha256": course_row["statement_sha256"],
        "selected_path_sha256": path_hash,
        "cost_sha256": object_hash(cost),
        "actor_envelope_sha256": object_hash(
            {key: value for key, value in receipt.items()
             if key not in {"verification", "receipt_sha256"}}
        ),
        "initial_state_sha256": initial_state_hash,
        "result": "verified",
        "kernel_exit_code": 0,
    }
    for key, expected in required_verification.items():
        if verification.get(key) != expected:
            raise ValueError(f"{problem_id}: verifier field {key} is not trusted/bound")

    try:
        vocabulary_size = int(len(tokenizer))
    except TypeError:
        vocabulary_size = int(getattr(tokenizer, "vocab_size", 0))
    if vocabulary_size <= 0:
        raise ValueError("tokenizer must expose a positive vocab_size")
    transitions: list[dict] = []
    previous_after = initial_state_hash
    path_length = len(path)
    if path_length > policy.value_bins:
        raise ValueError(
            f"{problem_id}: selected path has {path_length} tactics, exceeding "
            f"the {policy.value_bins}-bin value horizon; long paths are rejected, never clamped"
        )
    for index, step in enumerate(path):
        if (not isinstance(step, dict) or type(step.get("step_index")) is not int
                or step["step_index"] != index):
            raise ValueError(f"{problem_id}: malformed/noncontiguous path index {index}")
        before = str(step.get("state_before_sha256", ""))
        after = str(step.get("state_after_sha256", ""))
        if before != previous_after or len(after) != 64:
            raise ValueError(f"{problem_id}: broken state/action chain at step {index}")
        previous_after = after
        prompt = str(step.get("prompt", ""))
        action = str(step.get("action", ""))
        if not prompt.strip() or not action.strip():
            raise ValueError(f"{problem_id}: empty prompt/action at step {index}")
        prompt_ids = _validate_ids(
            step.get("prompt_token_ids"), vocabulary_size=vocabulary_size,
            label=f"{problem_id}:prompt_token_ids[{index}]",
        )
        recomputed_prompt = list(tokenizer.encode(prompt, add_special_tokens=True))
        if prompt_ids != recomputed_prompt:
            raise ValueError(f"{problem_id}: prompt token IDs disagree with the pinned tokenizer")
        raw_ids = _validate_ids(
            step.get("raw_completion_token_ids"), vocabulary_size=vocabulary_size,
            label=f"{problem_id}:raw_completion_token_ids[{index}]",
        )
        span = step.get("tactic_token_span")
        if (not isinstance(span, list) or len(span) != 2 or
                any(isinstance(item, bool) or not isinstance(item, int) for item in span)):
            raise ValueError(f"{problem_id}: invalid tactic_token_span at step {index}")
        start, stop = span
        if not 0 <= start < stop <= len(raw_ids):
            raise ValueError(f"{problem_id}: tactic token span is out of range at step {index}")
        action_ids = raw_ids[start:stop]
        if _normal_text(_decode(tokenizer, action_ids)) != _normal_text(action):
            raise ValueError(f"{problem_id}: action token IDs disagree with action text")

        # The actor does not own value supervision.  Derive it from the signed,
        # verified linear path and only accept an actor annotation as a redundant
        # consistency check.  For L tactics and zero-based index i the target is
        # exactly -(L-i), hence the terminal tactic is always -1.
        expected_value_target = -(path_length - index)
        value_target = float(expected_value_target)
        raw_target = step.get("value_target")
        if type(raw_target) not in {int, float}:
            raise ValueError(f"{problem_id}: value_target must be a finite numeric consistency field")
        if raw_target != expected_value_target:
            raise ValueError(
                f"{problem_id}: value_target mismatch at step {index}: actor reported "
                f"{raw_target!r}, verified selected path derives {value_target}"
            )
        transition_id = object_hash(
            {"request_sha256": request_hash, "step_index": index, "before": before,
             "action_token_ids": action_ids, "after": after}
        )
        transitions.append(
            {
                "prompt": prompt,
                "action": action,
                "value_target": value_target,
                "kind": "proof",
                "solved": True,
                "terminal_verified": True,
                "logprob_old": None,
                "reward": None,
                "extra": {
                    "transition_id": transition_id,
                    "receipt_id": receipt["receipt_id"],
                    "attempt_id": receipt["attempt_id"],
                    "problem_id": problem_id,
                    "family_index": int(course_row["family_index"]),
                    "variant_index": variant,
                    "wave_index": variant,
                    "statement_sha256": course_row["statement_sha256"],
                    "request_sha256": request_hash,
                    "verifier_receipt_sha256": verification["verification_receipt_sha256"],
                    "actor_config_sha256": policy.actor_config_sha256,
                    "budget_config_sha256": policy.budget_config_sha256,
                    "tokenizer_lock_sha256": policy.tokenizer_lock_sha256,
                    "prompt_token_ids": prompt_ids,
                    "action_token_ids": action_ids,
                    "path_index": index,
                    "path_length": path_length,
                    "selected_path_step": step,
                    "selected_path_sha256": path_hash,
                    "verification": verification,
                    "state_before_sha256": before,
                    "state_after_sha256": after,
                },
            }
        )
    if verification.get("final_state_sha256") != previous_after:
        raise ValueError(f"{problem_id}: verifier final state is not the selected path terminal state")
    return transitions


def canonical_receipt_to_transitions(receipt: dict, *, policy: ReplayPolicy,
                                     course_index: dict[str, dict], tokenizer) -> list[dict]:
    return _validate_receipt(receipt, policy, course_index, tokenizer)


def build_replay(receipts_path: str | Path, output_dir: str | Path, *,
                 policy: ReplayPolicy, tokenizer) -> dict:
    """Validate every receipt, then atomically publish a complete replay bundle."""
    source = Path(receipts_path).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to replace replay bundle: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    course, course_index_hash = _load_course_index(policy)
    stats = ReplayStats()
    transitions: list[dict] = []
    transition_ids: set[str] = set()
    receipt_ids: set[str] = set()
    attempt_ids: set[str] = set()
    # Budget names and the shared lock define these limits per problem, not per
    # receipt.  Keep this ledger across all attempts before publishing anything.
    per_problem_costs: dict[str, dict[str, int]] = {}
    for receipt in iter_receipts(source):
        stats.receipts += 1
        receipt_id = str(receipt.get("receipt_id", ""))
        attempt_id = str(receipt.get("attempt_id", ""))
        if not receipt_id or receipt_id in receipt_ids:
            raise ValueError(f"missing/duplicate receipt_id: {receipt_id!r}")
        if not attempt_id or attempt_id in attempt_ids:
            raise ValueError(f"missing/duplicate attempt_id: {attempt_id!r}")
        receipt_ids.add(receipt_id)
        attempt_ids.add(attempt_id)
        converted = _validate_receipt(receipt, policy, course, tokenizer)
        for transition in converted:
            transition_id = str(transition.get("extra", {}).get("transition_id", ""))
            if not transition_id or transition_id in transition_ids:
                raise ValueError(f"duplicate transition_id across receipts: {transition_id!r}")
            transition_ids.add(transition_id)
        generated_tokens = int(receipt["cost"]["generated_tokens"])
        lean_executions = int(receipt["cost"]["lean_tactic_executions"])
        problem_id = str(receipt["problem_id"])
        total = per_problem_costs.setdefault(
            problem_id,
            {"attempts": 0, "generated_tokens": 0, "lean_tactic_executions": 0},
        )
        total["attempts"] += 1
        total["generated_tokens"] += generated_tokens
        total["lean_tactic_executions"] += lean_executions
        if total["attempts"] > policy.max_attempts_per_problem:
            raise ValueError(
                f"{problem_id}: cumulative attempt budget exceeded: "
                f"{total['attempts']} > {policy.max_attempts_per_problem}"
            )
        if total["generated_tokens"] > policy.max_generated_tokens_per_problem:
            raise ValueError(
                f"{problem_id}: cumulative generated-token budget exceeded: "
                f"{total['generated_tokens']} > {policy.max_generated_tokens_per_problem}"
            )
        if (total["lean_tactic_executions"] >
                policy.max_lean_tactic_executions_per_problem):
            raise ValueError(
                f"{problem_id}: cumulative Lean-call budget exceeded: "
                f"{total['lean_tactic_executions']} > "
                f"{policy.max_lean_tactic_executions_per_problem}"
            )
        stats.generated_tokens += generated_tokens
        stats.lean_tactic_executions += lean_executions
        outcome = receipt.get("outcome")
        if outcome == "timeout":
            stats.skipped_timeout += 1
            continue
        if outcome == "infra_error":
            stats.skipped_infra_error += 1
            continue
        if outcome != "proof":
            stats.skipped_unsolved += 1
            continue
        stats.verified_proofs += 1
        stats.problems.add(problem_id)
        stats.transitions += len(converted)
        transitions.extend(converted)

    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    temporary.mkdir()
    replay_path = temporary / "transitions.jsonl"
    with replay_path.open("w", encoding="utf-8", newline="\n") as handle:
        for transition in transitions:
            handle.write(json.dumps(transition, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    ordered_problem_costs = {
        problem_id: per_problem_costs[problem_id] for problem_id in sorted(per_problem_costs)
    }
    manifest = {
        "schema_version": 2,
        "status": "complete",
        "source_receipts_path": str(source),
        "source_receipts_sha256": sha256_file(source),
        "course_manifest_sha256": policy.course_manifest_sha256,
        "ce_config_sha256": policy.ce_config_sha256,
        "course_index_sha256": course_index_hash,
        "max_wave": policy.max_wave,
        "actor_config_sha256": policy.actor_config_sha256,
        "budget_config_sha256": policy.budget_config_sha256,
        "tokenizer_lock_sha256": policy.tokenizer_lock_sha256,
        "trusted_verifier_id": policy.trusted_verifier_id,
        "trusted_verifier_lock_sha256": policy.trusted_verifier_lock_sha256,
        "trusted_verifier_public_key_sha256": sha256_bytes(
            bytes.fromhex(policy.trusted_verifier_public_key_hex)
        ),
        "distance_overflow_policy": policy.distance_overflow_policy,
        "value_bins": policy.value_bins,
        "budget_limits": {
            "max_attempts_per_problem": policy.max_attempts_per_problem,
            "max_generated_tokens_per_problem": policy.max_generated_tokens_per_problem,
            "max_lean_tactic_executions_per_problem": (
                policy.max_lean_tactic_executions_per_problem
            ),
        },
        "per_problem_costs": ordered_problem_costs,
        "per_problem_costs_sha256": object_hash(ordered_problem_costs),
        "transition_file": "transitions.jsonl",
        "transition_sha256": sha256_file(replay_path),
        "stats": stats.to_dict(),
    }
    manifest["manifest_payload_sha256"] = object_hash(manifest)
    manifest_path = temporary / "manifest.json"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(temporary)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)
    return manifest


def realprover_result_to_receipt(*args, **kwargs):
    raise RuntimeError(
        "Legacy REAL-Prover results lack schema-v2 request/verifier/token-chain bindings; "
        "regenerate them with the shared actor."
    )

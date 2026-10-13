"""Collect producer-owned Reap events into an immutable candidate-facts sidecar.

This collector intentionally cannot consume the legacy ``generation``/``eval``
observer stream.  Controlled choice provenance and canonical executor events are
mandatory; missing data is an infrastructure failure, never inferred evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Sequence


SCHEMA = "fate.reap.canonical_candidate_facts.v1"
MULTI_STATE_SCHEMA = "fate.reap.canonical_candidate_facts.v2"
OBSERVER_SCHEMA = "reap.training.observer.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CandidateFactsError(ValueError):
    """Producer evidence is incomplete, inconsistent, or mutable."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _derive_action_span(choice: dict[str, Any], raw_action: str, *, location: str) -> tuple[str, list[int]]:
    """Mirror the executor's end-anchored, token-boundary normalization."""
    logprobs = choice.get("logprobs")
    token_records = logprobs.get("content") if isinstance(logprobs, dict) else None
    if not isinstance(token_records, list) or not token_records:
        raise CandidateFactsError(f"{location} token stream missing")
    tokens: list[str] = []
    for token_record in token_records:
        token = token_record.get("token") if isinstance(token_record, dict) else None
        if not isinstance(token, str):
            raise CandidateFactsError(f"{location} token text invalid")
        tokens.append(token)
    rendered = ""
    raw_stop: int | None = None
    for token_index, token in enumerate(tokens):
        rendered += token
        if rendered == raw_action and raw_stop is None:
            raw_stop = token_index + 1
    if raw_stop is None:
        raise CandidateFactsError(f"{location} has no response-content token boundary")
    stop = raw_stop
    trimmed = raw_action.lstrip(" \t\n\r\f\v")
    if not trimmed.startswith("<think>"):
        return raw_action, [0, stop]
    parts = trimmed.split("</think>")
    if len(parts) != 2 or not parts[1].strip():
        raise CandidateFactsError(f"{location} has ambiguous/empty think-prefix normalization")
    action = parts[1]
    starts = [start for start in range(stop) if "".join(tokens[start:stop]) == action]
    if not starts:
        raise CandidateFactsError(f"{location} has no end-anchored action token span")
    # Empty rendered pieces are legitimate when a byte-level BPE token ends
    # inside a UTF-8 character.  Choose the earliest matching boundary so the
    # action span includes every raw token that contributed those bytes.
    return action, [starts[0], stop]


def _load_json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateFactsError(f"unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CandidateFactsError(f"JSON evidence must be an object: {path}")
    return raw, value


def _load_observer(path: Path, *, session_id: str, tree_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CandidateFactsError(f"unreadable observer: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateFactsError(f"malformed observer line {line_number}") from exc
        if not isinstance(record, dict):
            raise CandidateFactsError(f"observer line {line_number} is not an object")
        if record.get("schema_version") != OBSERVER_SCHEMA:
            raise CandidateFactsError(f"observer schema mismatch at line {line_number}")
        if record.get("session_id") != session_id or record.get("tree_id") != tree_id:
            raise CandidateFactsError(f"observer session/tree mismatch at line {line_number}")
        if record.get("sequence") != len(records):
            raise CandidateFactsError(f"observer sequence discontinuity at line {line_number}")
        records.append(record)
    if not records:
        raise CandidateFactsError("observer is empty")
    return records


def _policy_choices(receipt: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if receipt.get("status") != "committed" or receipt.get("schema_version") != "fate.policy_request.v2":
        raise CandidateFactsError("actor receipt is not a committed controlled policy receipt")
    expected_receipt_hash = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    if receipt.get("receipt_sha256") != expected_receipt_hash:
        raise CandidateFactsError("actor receipt canonical self-hash mismatch")
    request_id = receipt.get("request_id")
    candidates = receipt.get("candidates")
    response = receipt.get("openai_response")
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(request_id, str) or not request_id:
        raise CandidateFactsError("policy receipt lacks request_id")
    if not isinstance(candidates, list) or not candidates or not isinstance(choices, list):
        raise CandidateFactsError("policy receipt lacks candidates/openai_response choices")
    if len(candidates) != len(choices):
        raise CandidateFactsError("policy candidate/choice count mismatch")
    controlled: list[dict[str, Any]] = []
    for index, (candidate, choice) in enumerate(zip(candidates, choices, strict=True)):
        if not isinstance(candidate, dict) or not isinstance(choice, dict):
            raise CandidateFactsError(f"policy candidate {index} is not an object")
        message = choice.get("message")
        raw_action = message.get("content") if isinstance(message, dict) else None
        raw_index = choice.get("raw_sample_index")
        service_hash = choice.get("service_candidate_sha256")
        if raw_index != index or choice.get("index") != index or candidate.get("candidate_index") != index:
            raise CandidateFactsError(f"controlled choice index drift at {index}")
        if (not isinstance(raw_action, str) or not raw_action.strip()
                or candidate.get("returned_text") != raw_action):
            raise CandidateFactsError(f"controlled choice action mismatch at {index}")
        action, span = _derive_action_span(choice, raw_action, location=f"controlled choice {index}")
        if not isinstance(service_hash, str) or not SHA256_RE.fullmatch(service_hash):
            raise CandidateFactsError(f"controlled choice hash invalid at {index}")
        if candidate.get("service_candidate_sha256") != service_hash:
            raise CandidateFactsError(f"controlled choice/receipt hash mismatch at {index}")
        if candidate.get("request_id") != request_id:
            raise CandidateFactsError(f"controlled choice request mismatch at {index}")
        controlled.append({"action": action, "span": span, "service_hash": service_hash})
    return request_id, controlled


def _string(record: dict[str, Any], name: str, *, location: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value:
        raise CandidateFactsError(f"{location} lacks nonempty {name}")
    return value


def _validate_hash(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise CandidateFactsError(f"{location} is not lowercase SHA-256")
    return value


def _validate_event_pair(
    start: dict[str, Any], result: dict[str, Any], *, request_id: str,
    choices: list[dict[str, Any]], location: str,
) -> dict[str, Any]:
    event_id = _string(start, "event_id", location=location)
    for name in ("event_id", "trajectory_id", "generation_request_id", "state_id",
                 "depth", "parent_event_id", "partial_goal"):
        if result.get(name) != start.get(name):
            raise CandidateFactsError(f"{location} start/result {name} mismatch")
    if start.get("generation_request_id") != request_id:
        raise CandidateFactsError(f"{location} generation request mismatch")
    before = _string(start, "state_before_payload", location=location)
    before_hash = _validate_hash(start.get("state_before_sha256"), location=f"{location}.state_before")
    if hashlib.sha256(before.encode("utf-8")).hexdigest() != before_hash:
        raise CandidateFactsError(f"{location} before-state hash mismatch")
    if result.get("state_before_payload") != before or result.get("state_before_sha256") != before_hash:
        raise CandidateFactsError(f"{location} result before-state binding mismatch")
    after = result.get("state_after_payload")
    if not isinstance(after, str):
        raise CandidateFactsError(f"{location} lacks string state_after_payload")
    after_hash = _validate_hash(result.get("state_after_sha256"), location=f"{location}.state_after")
    if hashlib.sha256(after.encode("utf-8")).hexdigest() != after_hash:
        raise CandidateFactsError(f"{location} after-state hash mismatch")
    payload = _string(result, "executor_receipt_payload", location=location)
    executor_hash = _validate_hash(
        result.get("executor_receipt_sha256"), location=f"{location}.executor_receipt"
    )
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != executor_hash:
        raise CandidateFactsError(f"{location} executor receipt hash mismatch")
    try:
        payload_record = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CandidateFactsError(f"{location} executor receipt payload is invalid JSON") from exc
    if not isinstance(payload_record, dict):
        raise CandidateFactsError(f"{location} executor receipt payload is not an object")
    ignored = {"executor_receipt_payload", "executor_receipt_sha256"}
    if payload_record != {key: value for key, value in result.items() if key not in ignored}:
        raise CandidateFactsError(f"{location} executor receipt payload/event mismatch")

    indices = start.get("sample_indices")
    spans = start.get("sample_tactic_token_spans")
    service_hashes = start.get("service_candidate_sha256s")
    if (not isinstance(indices, list) or not indices
            or any(type(index) is not int for index in indices)
            or not isinstance(spans, list) or len(spans) != len(indices)
            or not isinstance(service_hashes, list) or len(service_hashes) != len(indices)):
        raise CandidateFactsError(f"{location} raw provenance arrays are malformed")
    action = _string(start, "action", location=location)
    for raw_index, span, service_hash in zip(indices, spans, service_hashes, strict=True):
        if raw_index < 0 or raw_index >= len(choices):
            raise CandidateFactsError(f"{location} raw sample index out of range")
        expected = choices[raw_index]
        if action != expected["action"] or span != expected["span"] or service_hash != expected["service_hash"]:
            raise CandidateFactsError(f"{location} controlled choice provenance mismatch at {raw_index}")
    survivor = start.get("survivor_sample_index")
    if survivor not in indices:
        raise CandidateFactsError(f"{location} survivor is outside its exact-action partition")
    disposition = result.get("execution_disposition")
    executions = result.get("lean_tactic_executions")
    did_execute = result.get("did_execute")
    if (disposition == "executed" and (executions != 1 or did_execute is not True)) or (
        disposition == "parse_rejected" and (executions != 0 or did_execute is not False)
    ) or disposition not in {"executed", "parse_rejected"}:
        raise CandidateFactsError(f"{location} execution disposition/count mismatch")
    status = result.get("verifier_status")
    if status not in {
        "verified_proof", "verified_disproof", "invalid_tactic", "unresolved",
        "timeout", "infrastructure_error",
    }:
        raise CandidateFactsError(f"{location} canonical verifier status invalid")
    parser_phase = result.get("parser_phase")
    transition = result.get("transition_applied")
    terminal = result.get("terminal")
    eval_result = result.get("eval_result")
    eval_ok = isinstance(eval_result, dict) and set(eval_result) == {"ok"}
    partial_goal = result.get("partial_goal")
    if type(partial_goal) is not bool:
        raise CandidateFactsError(f"{location} lacks boolean partial_goal")
    error_value = eval_result.get("error") if isinstance(eval_result, dict) else None
    if isinstance(error_value, dict) and len(error_value) == 1:
        error_kind = next(iter(error_value))
    elif isinstance(error_value, str):
        error_kind = error_value
    else:
        error_kind = None
    parse_error = error_kind in {"parseError", "forbiddenTactic"}
    if disposition == "parse_rejected":
        if (parser_phase != "rejected" or not parse_error or status != "invalid_tactic"
                or transition is not False or terminal is not False or after != before):
            raise CandidateFactsError(f"{location} contradictory parse-rejected semantics")
    else:
        if parser_phase != "accepted":
            raise CandidateFactsError(f"{location} executed result was not parser-accepted")
        if eval_ok:
            expected_status = "verified_proof" if terminal is True else "unresolved"
            if (transition is not True or status != expected_status or type(terminal) is not bool
                    or (partial_goal and terminal is True)):
                raise CandidateFactsError(f"{location} contradictory successful execution semantics")
        else:
            expected_status = "timeout" if error_kind == "tacticTimeout" else "invalid_tactic"
            if (transition is not False or terminal is not False or status != expected_status
                    or after != before):
                raise CandidateFactsError(f"{location} contradictory failed execution semantics")
    depth = start.get("depth")
    parent = start.get("parent_event_id")
    if type(depth) is not int or depth < 0 or (depth == 0 and parent is not None) or (
        depth > 0 and (not isinstance(parent, str) or not parent)
    ):
        raise CandidateFactsError(f"{location} depth/parent binding invalid")
    action_value = start.get("action_value")
    if not isinstance(action_value, (int, float)) or isinstance(action_value, bool) or not math.isfinite(action_value):
        raise CandidateFactsError(f"{location} action_value is not finite")
    return {
        "event_id": event_id,
        "trajectory_id": start["trajectory_id"],
        "action": action,
        "sample_indices": indices,
        "sample_tactic_token_spans": spans,
        "survivor_sample_index": survivor,
        "action_value": float(action_value),
        "verifier_status": status,
        "executor_receipt_sha256": executor_hash,
        "state_after_sha256": after_hash,
        "depth": depth,
        "parent_event_id": parent,
        "execution_disposition": disposition,
        "lean_tactic_executions": executions,
    }


def _atomic_publish(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    if path.exists() or pending.exists():
        raise CandidateFactsError(f"refusing to overwrite candidate facts: {path}")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(pending, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            if os.name != "nt":
                raise
    finally:
        pending.unlink(missing_ok=True)


def rebuild_candidate_facts(
    *, observer: Path, raw_tree: Path, result_json: Path, actor_receipt: Path,
    session_id: str, tree_id: str, expected_raw_count: int = 64,
) -> dict[str, Any]:
    """Rebuild candidate facts exclusively from the bound producer artifacts.

    This is intentionally side-effect free so the signing path can compare an
    untrusted sidecar with a fresh reconstruction before reading a private key.
    """
    if expected_raw_count <= 0:
        raise CandidateFactsError("expected_raw_count must be positive")
    for path in (observer, raw_tree, result_json, actor_receipt):
        if not path.is_file():
            raise CandidateFactsError(f"missing producer evidence: {path}")
    actor_raw, policy = _load_json_bytes(actor_receipt)
    actor_hash = hashlib.sha256(actor_raw).hexdigest()
    request_id, choices = _policy_choices(policy)
    if len(choices) != expected_raw_count:
        raise CandidateFactsError(
            f"policy raw count {len(choices)} does not match frozen count {expected_raw_count}"
        )
    records = _load_observer(observer, session_id=session_id, tree_id=tree_id)
    starts: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    paths: list[dict[str, Any]] = []
    for record in records:
        kind = record.get("kind")
        if kind == "canonical_candidate":
            event_id = _string(record, "event_id", location="canonical_candidate")
            if event_id in starts:
                raise CandidateFactsError(f"duplicate canonical candidate {event_id}")
            starts[event_id] = record
        elif kind == "canonical_candidate_result":
            event_id = _string(record, "event_id", location="canonical_candidate_result")
            if event_id in results:
                raise CandidateFactsError(f"duplicate canonical candidate result {event_id}")
            results[event_id] = record
        elif kind == "canonical_selected_path":
            paths.append(record)
    if not starts or set(starts) != set(results):
        # Keep this fail-closed, but report enough producer-local identity to
        # distinguish an interrupted/throwing Lean evaluation from a genuine
        # observer pairing bug.  Event ids are non-secret commitments and the
        # bounded preview avoids turning a malformed stream into unbounded log
        # output.
        missing_results = sorted(set(starts) - set(results))
        orphan_results = sorted(set(results) - set(starts))
        preview = 8
        raise CandidateFactsError(
            "canonical candidate starts/results are not one-to-one "
            f"(starts={len(starts)}, results={len(results)}, "
            f"missing_result_ids={missing_results[:preview]!r}, "
            f"orphan_result_ids={orphan_results[:preview]!r}, "
            f"missing_result_count={len(missing_results)}, "
            f"orphan_result_count={len(orphan_results)})"
        )
    if len(paths) != 1:
        raise CandidateFactsError("exactly one canonical_selected_path event is required")
    ordered_starts = sorted(starts.values(), key=lambda record: record["sequence"])
    candidates = [
        _validate_event_pair(
            start, results[start["event_id"]], request_id=request_id, choices=choices,
            location=f"event[{start['event_id']}]",
        )
        for start in ordered_starts
    ]
    expected_trajectory = f"trajectory:{session_id}:{tree_id}"
    if any(candidate["trajectory_id"] != expected_trajectory for candidate in candidates):
        raise CandidateFactsError("canonical candidates do not share the session/tree trajectory")
    mapped = [index for candidate in candidates for index in candidate["sample_indices"]]
    if sorted(mapped) != list(range(expected_raw_count)):
        raise CandidateFactsError("canonical candidates do not exactly partition raw samples")
    actions = [candidate["action"] for candidate in candidates]
    if len(actions) != len(set(actions)):
        raise CandidateFactsError("exact-action dedup emitted duplicate canonical actions")
    for candidate in candidates:
        expected_indices = [index for index, choice in enumerate(choices)
                            if choice["action"] == candidate["action"]]
        if candidate["sample_indices"] != expected_indices:
            raise CandidateFactsError("canonical action does not own its complete ordered raw partition")
    selected_record = paths[0]
    selected = selected_record.get("selected_event_ids")
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(event_id, str) or event_id not in starts for event_id in selected)
            or len(set(selected)) != len(selected)):
        raise CandidateFactsError("selected path references missing or duplicate events")
    if selected_record.get("terminal_event_id") != selected[-1] or selected_record.get("outcome") != "proof":
        raise CandidateFactsError("selected path terminal/outcome binding mismatch")
    proof_script = _string(selected_record, "proof_script", location="canonical_selected_path")
    proof_hash = _validate_hash(
        selected_record.get("proof_script_sha256"), location="canonical_selected_path.proof_script"
    )
    if hashlib.sha256(proof_script.encode("utf-8")).hexdigest() != proof_hash:
        raise CandidateFactsError("selected path proof-script hash mismatch")
    selected_payload = _string(
        selected_record, "selected_path_receipt_payload", location="canonical_selected_path"
    )
    selected_receipt_hash = _validate_hash(
        selected_record.get("selected_path_receipt_sha256"),
        location="canonical_selected_path.selected_path_receipt",
    )
    if hashlib.sha256(selected_payload.encode("utf-8")).hexdigest() != selected_receipt_hash:
        raise CandidateFactsError("selected path receipt hash mismatch")
    try:
        selected_payload_record = json.loads(selected_payload)
    except json.JSONDecodeError as exc:
        raise CandidateFactsError("selected path receipt payload is invalid JSON") from exc
    selected_ignored = {"selected_path_receipt_payload", "selected_path_receipt_sha256"}
    if selected_payload_record != {
        key: value for key, value in selected_record.items() if key not in selected_ignored
    }:
        raise CandidateFactsError("selected path receipt payload/event mismatch")
    result_raw, terminal_result = _load_json_bytes(result_json)
    if (terminal_result.get("schema_version") != "reap.training.result.v1"
            or terminal_result.get("session_id") != session_id
            or terminal_result.get("solved") is not True
            or terminal_result.get("status") != "solved"
            or terminal_result.get("proof_script") != proof_script):
        raise CandidateFactsError("selected path proof script does not match terminal result.json")
    result_hash = hashlib.sha256(result_raw).hexdigest()
    by_id = {candidate["event_id"]: candidate for candidate in candidates}
    for index, event_id in enumerate(selected):
        candidate = by_id[event_id]
        if index == 0:
            if candidate["depth"] != 0 or candidate["parent_event_id"] is not None:
                raise CandidateFactsError("selected path does not start at root depth")
        else:
            previous = by_id[selected[index - 1]]
            if (candidate["parent_event_id"] != previous["event_id"]
                    or candidate["depth"] != previous["depth"] + 1):
                raise CandidateFactsError("selected path parent/depth chain is discontinuous")
            if starts[event_id].get("state_before_sha256") != previous["state_after_sha256"]:
                raise CandidateFactsError("selected path state hash chain is discontinuous")
    if by_id[selected[-1]]["verifier_status"] != "verified_proof":
        raise CandidateFactsError("selected terminal event is not a verified proof")
    if len(selected) == 1:
        ascii_ws = " \t\n\r\f\v"
        if by_id[selected[0]]["action"].strip(ascii_ws) != proof_script.strip(ascii_ws):
            raise CandidateFactsError("one-step selected action does not render the terminal proof script")
    terminal_id = selected[-1]
    terminal_candidate = by_id[terminal_id]
    composite_hash = canonical_sha256({
        "candidate_result_sha256": terminal_candidate["executor_receipt_sha256"],
        "selected_path_receipt_sha256": selected_receipt_hash,
    })
    terminal_candidate["executor_receipt_sha256"] = composite_hash
    initial_start = starts[selected[0]]
    initial_hash = initial_start["state_before_sha256"]
    facts: dict[str, Any] = {
        "schema_version": SCHEMA,
        "session_id": session_id,
        "observer_sha256": sha256_file(observer),
        "raw_tree_sha256": sha256_file(raw_tree),
        "policy_receipt_sha256": actor_hash,
        "state_id": initial_start["state_id"],
        "state_sha256": initial_hash,
        "initial_state_sha256": initial_hash,
        "candidates": candidates,
        "selected_event_ids": selected,
        "selected_proof_script_sha256": proof_hash,
    }
    facts["receipt_sha256"] = canonical_sha256(facts)
    if hashlib.sha256(actor_receipt.read_bytes()).hexdigest() != actor_hash:
        raise CandidateFactsError("actor receipt changed during collection")
    if hashlib.sha256(result_json.read_bytes()).hexdigest() != result_hash:
        raise CandidateFactsError("result.json changed during collection")
    return facts


def rebuild_candidate_facts_envelope(
    *, observer: Path, raw_tree: Path, result_json: Path,
    actor_receipts: Sequence[Path], session_id: str, tree_id: str,
    expected_raw_count: int = 64,
) -> dict[str, Any]:
    """Rebuild one canonical multi-request/multi-state search envelope.

    Each policy request owns exactly one search state and its complete raw
    sample partition.  The selected path may cross requests/states, but every
    edge is checked against the producer-owned parent/depth and state hashes.
    No ordering coincidence between receipt filenames and observer events is
    used as evidence.
    """
    if expected_raw_count <= 0:
        raise CandidateFactsError("expected_raw_count must be positive")
    if not actor_receipts:
        raise CandidateFactsError("at least one actor receipt is required")
    producer_paths = (observer, raw_tree, result_json, *actor_receipts)
    for path in producer_paths:
        if not path.is_file():
            raise CandidateFactsError(f"missing producer evidence: {path}")
    initial_hashes = {path.resolve(): sha256_file(path) for path in producer_paths}

    choices_by_request: dict[str, list[dict[str, Any]]] = {}
    receipt_path_by_request: dict[str, Path] = {}
    policy_bindings: list[dict[str, str]] = []
    for actor_receipt in actor_receipts:
        actor_raw, policy = _load_json_bytes(actor_receipt)
        request_id, choices = _policy_choices(policy)
        if request_id in choices_by_request:
            raise CandidateFactsError(f"duplicate policy request_id: {request_id}")
        if len(choices) != expected_raw_count:
            raise CandidateFactsError(
                f"policy request {request_id} raw count {len(choices)} does not match "
                f"frozen count {expected_raw_count}"
            )
        choices_by_request[request_id] = choices
        receipt_path_by_request[request_id] = actor_receipt
        policy_bindings.append({
            "request_id": request_id,
            "policy_receipt_sha256": hashlib.sha256(actor_raw).hexdigest(),
        })
    policy_bindings.sort(key=lambda item: item["request_id"])

    records = _load_observer(observer, session_id=session_id, tree_id=tree_id)
    starts: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    selected_records: list[dict[str, Any]] = []
    for record in records:
        kind = record.get("kind")
        if kind == "canonical_candidate":
            event_id = _string(record, "event_id", location="canonical_candidate")
            if event_id in starts:
                raise CandidateFactsError(f"duplicate canonical candidate {event_id}")
            starts[event_id] = record
        elif kind == "canonical_candidate_result":
            event_id = _string(record, "event_id", location="canonical_candidate_result")
            if event_id in results:
                raise CandidateFactsError(f"duplicate canonical candidate result {event_id}")
            results[event_id] = record
        elif kind == "canonical_selected_path":
            selected_records.append(record)
    if not starts or set(starts) != set(results):
        raise CandidateFactsError("canonical candidate starts/results are not one-to-one")
    if len(selected_records) != 1:
        raise CandidateFactsError("exactly one canonical_selected_path event is required")

    ordered_starts = sorted(starts.values(), key=lambda record: record["sequence"])
    state_groups: dict[str, dict[str, Any]] = {}
    request_order: list[str] = []
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for start in ordered_starts:
        request_id = start.get("generation_request_id")
        if request_id not in choices_by_request:
            raise CandidateFactsError(
                f"event {start.get('event_id')} references unknown policy request {request_id!r}"
            )
        candidate = _validate_event_pair(
            start, results[start["event_id"]], request_id=request_id,
            choices=choices_by_request[request_id], location=f"event[{start['event_id']}]",
        )
        candidate_by_id[candidate["event_id"]] = candidate
        state_id = _string(start, "state_id", location=f"event[{start['event_id']}]")
        state_sha256 = _validate_hash(
            start.get("state_before_sha256"), location=f"event[{start['event_id']}].state_before"
        )
        group = state_groups.get(request_id)
        if group is None:
            request_order.append(request_id)
            group = state_groups[request_id] = {
                "generation_request_id": request_id,
                "state_id": state_id,
                "state_sha256": state_sha256,
                "candidates": [],
            }
        elif group["state_id"] != state_id or group["state_sha256"] != state_sha256:
            raise CandidateFactsError(
                f"policy request {request_id} is reused across distinct search states"
            )
        group["candidates"].append(candidate)

    if set(state_groups) != set(choices_by_request):
        unused = sorted(set(choices_by_request) - set(state_groups))
        raise CandidateFactsError("policy receipts without canonical state events: " + ", ".join(unused))
    expected_trajectory = f"trajectory:{session_id}:{tree_id}"
    for request_id in request_order:
        group = state_groups[request_id]
        candidates = group["candidates"]
        if any(item["trajectory_id"] != expected_trajectory for item in candidates):
            raise CandidateFactsError("canonical candidates do not share the session/tree trajectory")
        mapped = [index for item in candidates for index in item["sample_indices"]]
        if sorted(mapped) != list(range(expected_raw_count)):
            raise CandidateFactsError(
                f"policy request {request_id} does not exactly partition raw samples"
            )
        actions = [item["action"] for item in candidates]
        if len(actions) != len(set(actions)):
            raise CandidateFactsError(
                f"policy request {request_id} emitted duplicate canonical actions"
            )
        choices = choices_by_request[request_id]
        for candidate in candidates:
            expected_indices = [
                index for index, choice in enumerate(choices)
                if choice["action"] == candidate["action"]
            ]
            if candidate["sample_indices"] != expected_indices:
                raise CandidateFactsError(
                    f"policy request {request_id} action does not own its ordered raw partition"
                )

    selected_record = selected_records[0]
    selected = selected_record.get("selected_event_ids")
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(event_id, str) or event_id not in starts for event_id in selected)
            or len(set(selected)) != len(selected)):
        raise CandidateFactsError("selected path references missing or duplicate events")
    if (selected_record.get("terminal_event_id") != selected[-1]
            or selected_record.get("outcome") != "proof"):
        raise CandidateFactsError("selected path terminal/outcome binding mismatch")
    proof_script = _string(selected_record, "proof_script", location="canonical_selected_path")
    proof_hash = _validate_hash(
        selected_record.get("proof_script_sha256"),
        location="canonical_selected_path.proof_script",
    )
    if hashlib.sha256(proof_script.encode("utf-8")).hexdigest() != proof_hash:
        raise CandidateFactsError("selected path proof-script hash mismatch")
    selected_payload = _string(
        selected_record, "selected_path_receipt_payload", location="canonical_selected_path"
    )
    selected_receipt_hash = _validate_hash(
        selected_record.get("selected_path_receipt_sha256"),
        location="canonical_selected_path.selected_path_receipt",
    )
    if hashlib.sha256(selected_payload.encode("utf-8")).hexdigest() != selected_receipt_hash:
        raise CandidateFactsError("selected path receipt hash mismatch")
    try:
        selected_payload_record = json.loads(selected_payload)
    except json.JSONDecodeError as exc:
        raise CandidateFactsError("selected path receipt payload is invalid JSON") from exc
    selected_ignored = {"selected_path_receipt_payload", "selected_path_receipt_sha256"}
    if selected_payload_record != {
        key: value for key, value in selected_record.items() if key not in selected_ignored
    }:
        raise CandidateFactsError("selected path receipt payload/event mismatch")

    result_raw, terminal_result = _load_json_bytes(result_json)
    if (terminal_result.get("schema_version") != "reap.training.result.v1"
            or terminal_result.get("session_id") != session_id
            or terminal_result.get("solved") is not True
            or terminal_result.get("status") != "solved"
            or terminal_result.get("proof_script") != proof_script):
        raise CandidateFactsError("selected path proof script does not match terminal result.json")
    for index, event_id in enumerate(selected):
        candidate = candidate_by_id[event_id]
        start = starts[event_id]
        if index == 0:
            if candidate["depth"] != 0 or candidate["parent_event_id"] is not None:
                raise CandidateFactsError("selected path does not start at root depth")
        else:
            previous = candidate_by_id[selected[index - 1]]
            if (candidate["parent_event_id"] != previous["event_id"]
                    or candidate["depth"] != previous["depth"] + 1):
                raise CandidateFactsError("selected path parent/depth chain is discontinuous")
            if start.get("state_before_sha256") != previous["state_after_sha256"]:
                raise CandidateFactsError("selected path state hash chain is discontinuous")
    if candidate_by_id[selected[-1]]["verifier_status"] != "verified_proof":
        raise CandidateFactsError("selected terminal event is not a verified proof")

    terminal_candidate = candidate_by_id[selected[-1]]
    terminal_candidate["executor_receipt_sha256"] = canonical_sha256({
        "candidate_result_sha256": terminal_candidate["executor_receipt_sha256"],
        "selected_path_receipt_sha256": selected_receipt_hash,
    })
    initial_hash = starts[selected[0]]["state_before_sha256"]
    facts: dict[str, Any] = {
        "schema_version": MULTI_STATE_SCHEMA,
        "session_id": session_id,
        "observer_sha256": sha256_file(observer),
        "raw_tree_sha256": sha256_file(raw_tree),
        "policy_receipts": policy_bindings,
        "initial_state_sha256": initial_hash,
        "states": [state_groups[request_id] for request_id in request_order],
        "selected_event_ids": selected,
        "selected_proof_script_sha256": proof_hash,
    }
    facts["receipt_sha256"] = canonical_sha256(facts)

    # Fail closed if any producer file changed while it was being reconstructed.
    if hashlib.sha256(result_json.read_bytes()).hexdigest() != hashlib.sha256(result_raw).hexdigest():
        raise CandidateFactsError("result.json changed during collection")
    for path, expected_hash in initial_hashes.items():
        if sha256_file(path) != expected_hash:
            raise CandidateFactsError(f"producer evidence changed during collection: {path}")
    return facts


def collect_candidate_facts(
    *, observer: Path, raw_tree: Path, result_json: Path, actor_receipt: Path, output: Path,
    session_id: str, tree_id: str, expected_raw_count: int = 64,
) -> dict[str, Any]:
    """Validate terminal producer evidence and exclusively publish one sidecar."""
    facts = rebuild_candidate_facts(
        observer=observer, raw_tree=raw_tree, result_json=result_json,
        actor_receipt=actor_receipt, session_id=session_id, tree_id=tree_id,
        expected_raw_count=expected_raw_count,
    )
    _atomic_publish(output, facts)
    return facts


def collect_candidate_facts_envelope(
    *, observer: Path, raw_tree: Path, result_json: Path,
    actor_receipts: Sequence[Path], output: Path, session_id: str, tree_id: str,
    expected_raw_count: int = 64,
) -> dict[str, Any]:
    """Exclusively publish the canonical v2 multi-state join sidecar."""
    facts = rebuild_candidate_facts_envelope(
        observer=observer, raw_tree=raw_tree, result_json=result_json,
        actor_receipts=actor_receipts, session_id=session_id, tree_id=tree_id,
        expected_raw_count=expected_raw_count,
    )
    _atomic_publish(output, facts)
    return facts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--raw-tree", type=Path, required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument(
        "--actor-receipt", type=Path, required=True, action="append",
        help="committed policy receipt; repeat once per canonical search state",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--tree-id", required=True)
    parser.add_argument("--expected-raw-count", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        facts = collect_candidate_facts_envelope(
            observer=args.observer, raw_tree=args.raw_tree, result_json=args.result_json,
            actor_receipts=tuple(args.actor_receipt),
            output=args.output, session_id=args.session_id, tree_id=args.tree_id,
            expected_raw_count=args.expected_raw_count,
        )
    except CandidateFactsError as exc:
        parser.exit(2, f"fail closed: {exc}\n")
    print(json.dumps({"status": "published", "receipt_sha256": facts["receipt_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

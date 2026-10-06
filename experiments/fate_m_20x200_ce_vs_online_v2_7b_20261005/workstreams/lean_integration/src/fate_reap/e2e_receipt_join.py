"""Fail-closed join for immutable REAL-Prover/Reap smoke evidence.

The policy receipt owns generation facts.  Reap must separately emit a
session-bound candidate-facts receipt for the search/executor facts that are
not present in the OpenAI response.  This module refuses to infer those facts
from a pretty-printed tree or from ordering coincidences in observer events.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from .candidate_facts import (
    MULTI_STATE_SCHEMA,
    CandidateFactsError,
    rebuild_candidate_facts_envelope,
)
from .strict_replay import ReplayInputs, run_strict_replay


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_FACTS_SCHEMA = MULTI_STATE_SCHEMA
GAP_SCHEMA = "fate.reap.canonical_join_gap.v1"


class JoinError(ValueError):
    """Evidence cannot be joined without inventing a fact."""


@dataclass(frozen=True)
class JoinGap:
    code: str
    location: str
    detail: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _io_path(path: Path) -> Path:
    """Return a Windows long-path spelling without changing evidence identity."""
    resolved = path.resolve()
    text = str(resolved)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path("\\\\?\\" + text)
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _io_path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(_io_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JoinError(f"unreadable JSON evidence: {path}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = _io_path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise JoinError(f"unreadable JSONL evidence: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JoinError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise JoinError(f"non-object JSONL record at {path}:{line_number}")
        records.append(value)
    return records


def _one(items: Sequence[Path], label: str) -> Path:
    if len(items) != 1:
        raise JoinError(f"expected exactly one {label}, found {len(items)}")
    return items[0]


def _run_paths(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    return {
        "run_root": root,
        "report": root / "report.json",
        "done": root / "DONE.json",
        "sessions": root / "sessions.jsonl",
        "observer": root / "observer.jsonl",
        "raw_tree": root / "raw_tree.json",
        "result": root / "session" / "result.json",
        "policies": tuple(sorted(root.glob("actor_receipts/*/policy_requests/*.json"))),
    }


def _rebuild_bound_candidate_facts(
    paths: Mapping[str, Any], *, session_id: str, raw_count: int,
) -> dict[str, Any]:
    observer = _load_jsonl(paths["observer"])
    canonical_kinds = {
        "canonical_candidate", "canonical_candidate_result", "canonical_selected_path"
    }
    tree_ids = {
        record.get("tree_id") for record in observer
        if record.get("kind") in canonical_kinds
    }
    if len(tree_ids) != 1 or not isinstance(next(iter(tree_ids), None), str):
        raise CandidateFactsError("run requires exactly one nonempty canonical tree_id")
    policies = paths.get("policies")
    if not isinstance(policies, tuple) or not policies:
        raise CandidateFactsError("run has no committed policy receipts")
    return rebuild_candidate_facts_envelope(
        observer=paths["observer"], raw_tree=paths["raw_tree"],
        result_json=paths["result"], actor_receipts=policies,
        session_id=session_id, tree_id=next(iter(tree_ids)),
        expected_raw_count=raw_count,
    )


def audit_run(run_root: Path, *, candidate_facts: Path | None = None) -> dict[str, Any]:
    """Validate immutable bindings and report facts missing from the join.

    This function intentionally does not accept a signing key.  A caller can
    therefore run the audit on untrusted/incomplete evidence without risking a
    signature or a Lean invocation.
    """
    paths = _run_paths(run_root)
    report = _load_json(paths["report"])
    done = _load_json(paths["done"])
    sessions = _load_jsonl(paths["sessions"])
    observer = _load_jsonl(paths["observer"])
    result = _load_json(paths["result"])
    raw_tree = _load_json(paths["raw_tree"])
    policy_paths = paths["policies"]
    if not isinstance(policy_paths, tuple) or not policy_paths:
        raise JoinError("run has no policy receipts")
    policies = [_load_json(path) for path in policy_paths]
    if not isinstance(report, dict) or report.get("schema_version") != "fate.policy_service.real_reap_e2e_smoke.v1":
        raise JoinError("run report has the wrong schema")
    if report.get("result") != "PASS" or done.get("state") != "DONE":
        raise JoinError("run is not terminal PASS/DONE evidence")
    if done.get("report_sha256") != sha256_file(paths["report"]):
        raise JoinError("DONE report hash mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise JoinError("run report lacks artifact bindings")
    session_artifacts = artifacts.get("session")
    exact_artifacts = {
        "observer": (paths["observer"], artifacts.get("observer")),
        "raw_tree": (paths["raw_tree"], artifacts.get("raw_tree")),
        "session manifest": (paths["sessions"], artifacts.get("manifest")),
        "session result": (
            paths["result"],
            session_artifacts.get("result.json")
            if isinstance(session_artifacts, dict) else None,
        ),
    }
    for name, (path, binding) in exact_artifacts.items():
        expected = binding.get("sha256") if isinstance(binding, dict) else None
        if expected != sha256_file(path):
            raise JoinError(f"immutable {name} hash mismatch")
    policy_bindings = artifacts.get("policy_receipts")
    if (not isinstance(policy_bindings, list)
            or any(not isinstance(binding, dict)
                   or not isinstance(binding.get("sha256"), str)
                   for binding in policy_bindings)):
        raise JoinError("immutable policy receipt bindings are malformed")
    bound_policy_hashes = sorted(binding["sha256"] for binding in policy_bindings)
    actual_policy_hashes = sorted(sha256_file(path) for path in policy_paths)
    if bound_policy_hashes != actual_policy_hashes:
        raise JoinError("immutable policy receipt set/hash mismatch")
    if len(sessions) != 1:
        raise JoinError("join supports exactly one bounded smoke session")
    session = sessions[0]
    session_id = report.get("session_id")
    for label, value in {
        "session manifest": session.get("session_id"),
        "result": result.get("session_id"),
    }.items():
        if value != session_id:
            raise JoinError(f"{label} session_id mismatch")
    if any(not isinstance(policy, dict) or policy.get("session_id") != session_id
           for policy in policies):
        raise JoinError("policy receipt session_id mismatch")
    sequences = [record.get("sequence") for record in observer]
    if sequences != list(range(len(observer))):
        raise JoinError("observer sequence is not contiguous from zero")
    if any(record.get("session_id") != session_id for record in observer):
        raise JoinError("observer contains a cross-session record")
    generations = [record for record in observer if record.get("kind") == "generation"]
    evaluations = [record for record in observer if record.get("kind") == "eval"]
    checkpoints = [record for record in observer if record.get("kind") == "checkpoint"]
    request_ids: set[str] = set()
    policy_candidate_counts: list[int] = []
    for policy in policies:
        request_id = policy.get("request_id")
        if (policy.get("schema_version") != "fate.policy_request.v2"
                or policy.get("status") != "committed"
                or not isinstance(request_id, str) or not request_id
                or request_id in request_ids
                or policy.get("receipt_sha256") != _canonical_sha256({
                    key: value for key, value in policy.items() if key != "receipt_sha256"
                })):
            raise JoinError("policy receipt set is not unique self-consistent committed v2 evidence")
        request_ids.add(request_id)
        generation_receipt = policy.get("generation_receipt")
        if (not isinstance(generation_receipt, dict)
                or policy.get("generation_receipt_sha256") != _canonical_sha256(generation_receipt)):
            raise JoinError("policy generation receipt commitment mismatch")
        policy_candidates = policy.get("candidates")
        if not isinstance(policy_candidates, list) or len(policy_candidates) != 64:
            raise JoinError("formal policy receipt does not contain exactly 64 raw samples")
        policy_candidate_counts.append(len(policy_candidates))
    if (not checkpoints or checkpoints[-1].get("root_is_solved") is not True
            or result.get("solved") is not True or result.get("status") != "solved"):
        raise JoinError("smoke evidence does not establish a Reap-solved root")
    if not isinstance(raw_tree, dict) or raw_tree.get("root_index") != 0:
        raise JoinError("raw tree lacks its bound root")

    gaps: list[JoinGap] = []
    facts_value: Any = None
    if candidate_facts is None:
        required = (
            "raw sample index partition (all 0..63)",
            "per-sample tactic token spans after Reap normalization",
            "stable execution event/trajectory IDs and selected-path order",
            "session-bound executor receipt SHA-256 for every unique tactic",
            "explicit successor-state SHA-256 and parent/depth bindings",
            "canonical verifier status, execution disposition, and Lean execution count",
        )
        gaps.append(JoinGap(
            "missing_candidate_facts_receipt",
            "observer.jsonl",
            "observer has generation/eval/checkpoint events but no immutable "
            f"{CANDIDATE_FACTS_SCHEMA} receipt containing: " + "; ".join(required),
        ))
    else:
        facts_value = _load_json(candidate_facts)
        gaps.extend(_validate_candidate_facts(
            facts_value,
            session_id=str(session_id),
            observer_sha256=sha256_file(paths["observer"]),
            raw_tree_sha256=sha256_file(paths["raw_tree"]),
            policy_receipts={
                policy["request_id"]: sha256_file(path)
                for policy, path in zip(policies, policy_paths, strict=True)
            },
            raw_count=policy_candidate_counts[0],
            selected_proof_script=result.get("proof_script"),
        ))
        try:
            rebuilt = _rebuild_bound_candidate_facts(
                paths, session_id=str(session_id), raw_count=policy_candidate_counts[0]
            )
        except CandidateFactsError as exc:
            gaps.append(JoinGap(
                "candidate_facts_reconstruction", "bound producer artifacts", str(exc),
            ))
        else:
            if facts_value != rebuilt:
                differing = sorted(
                    key for key in set(facts_value) | set(rebuilt)
                    if facts_value.get(key) != rebuilt.get(key)
                )
                gaps.append(JoinGap(
                    "candidate_facts_reconstruction", "candidate_facts",
                    "sidecar differs from fresh producer-artifact reconstruction in: "
                    + ", ".join(differing),
                ))

    return {
        "schema_version": GAP_SCHEMA,
        "status": "ready" if not gaps else "blocked_missing_executor_evidence",
        "run_root": str(paths["run_root"]),
        "session_id": session_id,
        "immutable_inputs": {
            "report_sha256": sha256_file(paths["report"]),
            "sessions_sha256": sha256_file(paths["sessions"]),
            "observer_sha256": sha256_file(paths["observer"]),
            "raw_tree_sha256": sha256_file(paths["raw_tree"]),
            "policy_receipts_sha256": {
                policy["request_id"]: sha256_file(path)
                for policy, path in zip(policies, policy_paths, strict=True)
            },
            "result_sha256": sha256_file(paths["result"]),
        },
        "observed_counts": {
            "policy_requests": len(policies),
            "raw_policy_samples": sum(policy_candidate_counts),
            "unique_generation_events": len(generations),
            "eval_events": len(evaluations),
            "checkpoint_events": len(checkpoints),
        },
        "candidate_facts_sha256": (
            sha256_file(candidate_facts) if candidate_facts is not None else None
        ),
        "gaps": [asdict(gap) for gap in gaps],
        "private_key_read": False,
        "lean_invoked": False,
        "signed": False,
        "converted": False,
    }


def _validate_candidate_facts(
    facts: Any,
    *,
    session_id: str,
    observer_sha256: str,
    raw_tree_sha256: str,
    policy_receipts: Mapping[str, str],
    raw_count: int,
    selected_proof_script: Any,
) -> list[JoinGap]:
    gaps: list[JoinGap] = []
    if not isinstance(facts, dict) or facts.get("schema_version") != CANDIDATE_FACTS_SCHEMA:
        return [JoinGap("candidate_facts_schema", "candidate_facts", "wrong or missing schema")]
    top_fields = {
        "schema_version", "session_id", "observer_sha256", "raw_tree_sha256",
        "policy_receipts", "initial_state_sha256", "states", "selected_event_ids",
        "selected_proof_script_sha256", "receipt_sha256",
    }
    extra_top = sorted(set(facts) - top_fields)
    if extra_top:
        gaps.append(JoinGap(
            "candidate_facts_fields", "candidate_facts",
            "unrecognized fields: " + ", ".join(extra_top),
        ))
    exact = {
        "session_id": session_id,
        "observer_sha256": observer_sha256,
        "raw_tree_sha256": raw_tree_sha256,
    }
    for field, expected in exact.items():
        if facts.get(field) != expected:
            gaps.append(JoinGap("candidate_facts_binding", field, f"expected {expected}"))
    bindings = facts.get("policy_receipts")
    observed_bindings: dict[str, str] = {}
    if not isinstance(bindings, list):
        gaps.append(JoinGap("policy_receipts", "policy_receipts", "list required"))
    else:
        for index, binding in enumerate(bindings):
            if (not isinstance(binding, dict)
                    or set(binding) != {"request_id", "policy_receipt_sha256"}
                    or not isinstance(binding.get("request_id"), str)
                    or not SHA256_RE.fullmatch(str(binding.get("policy_receipt_sha256", "")))
                    or binding["request_id"] in observed_bindings):
                gaps.append(JoinGap(
                    "policy_receipts", f"policy_receipts[{index}]",
                    "unique request_id and lowercase SHA-256 required",
                ))
                continue
            observed_bindings[binding["request_id"]] = binding["policy_receipt_sha256"]
    if observed_bindings != dict(policy_receipts):
        gaps.append(JoinGap(
            "candidate_facts_binding", "policy_receipts",
            "must exactly bind every immutable policy receipt by request_id",
        ))
    initial_state_sha256 = facts.get("initial_state_sha256")
    if not isinstance(initial_state_sha256, str) or not SHA256_RE.fullmatch(initial_state_sha256):
        gaps.append(JoinGap(
            "candidate_facts_field", "initial_state_sha256", "missing or invalid",
        ))
    states = facts.get("states")
    if not isinstance(states, list) or not states:
        gaps.append(JoinGap(
            "candidate_facts_states", "states", "nonempty state list required",
        ))
        return gaps
    expected_proof_hash = (
        hashlib.sha256(selected_proof_script.encode("utf-8")).hexdigest()
        if isinstance(selected_proof_script, str) else None
    )
    if facts.get("selected_proof_script_sha256") != expected_proof_hash:
        gaps.append(JoinGap(
            "selected_proof_binding", "selected_proof_script_sha256",
            "must equal the exact terminal result.json proof script SHA-256",
        ))
    required = {
        "event_id", "trajectory_id", "action", "sample_indices",
        "sample_tactic_token_spans", "survivor_sample_index", "action_value",
        "verifier_status", "executor_receipt_sha256", "state_after_sha256",
        "depth", "parent_event_id", "execution_disposition", "lean_tactic_executions",
    }
    event_ids: set[str] = set()
    candidate_by_id: dict[str, dict[str, Any]] = {}
    state_by_event: dict[str, dict[str, Any]] = {}
    state_request_ids: set[str] = set()
    for state_index, state in enumerate(states):
        state_location = f"states[{state_index}]"
        if (not isinstance(state, dict)
                or set(state) != {"generation_request_id", "state_id", "state_sha256", "candidates"}):
            gaps.append(JoinGap("state_fields", state_location, "exact canonical state fields required"))
            continue
        request_id = state.get("generation_request_id")
        if (not isinstance(request_id, str) or request_id not in policy_receipts
                or request_id in state_request_ids):
            gaps.append(JoinGap("state_request", state_location, "unique bound request_id required"))
        else:
            state_request_ids.add(request_id)
        if not isinstance(state.get("state_id"), str) or not state["state_id"]:
            gaps.append(JoinGap("state_id", state_location, "nonempty state_id required"))
        if (not isinstance(state.get("state_sha256"), str)
                or not SHA256_RE.fullmatch(state["state_sha256"])):
            gaps.append(JoinGap("state_hash", state_location, "lowercase SHA-256 required"))
        candidates = state.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            gaps.append(JoinGap("candidate_facts_candidates", state_location, "nonempty list required"))
            continue
        mapped: list[int] = []
        for candidate_index, candidate in enumerate(candidates):
            location = f"{state_location}.candidates[{candidate_index}]"
            if not isinstance(candidate, dict):
                gaps.append(JoinGap("candidate_type", location, "object required"))
                continue
            if set(candidate) != required:
                gaps.append(JoinGap("candidate_fields", location, "exact canonical fields required"))
                continue
            indices = candidate.get("sample_indices")
            spans = candidate.get("sample_tactic_token_spans")
            if not isinstance(indices, list) or not all(type(item) is int for item in indices):
                gaps.append(JoinGap("sample_indices", location, "integer list required"))
            else:
                mapped.extend(indices)
            if (not isinstance(spans, list) or not isinstance(indices, list)
                    or len(spans) != len(indices) or any(
                        not isinstance(span, list) or len(span) != 2
                        or any(type(value) is not int for value in span)
                        or span[0] < 0 or span[1] <= span[0] for span in spans
                    )):
                gaps.append(JoinGap("tactic_spans", location, "one valid span per sample required"))
            if not isinstance(indices, list) or candidate.get("survivor_sample_index") not in indices:
                gaps.append(JoinGap("survivor_sample_index", location, "must name one mapped sample"))
            event_id = candidate.get("event_id")
            if not isinstance(event_id, str) or not event_id or event_id in event_ids:
                gaps.append(JoinGap("event_id", location, "nonempty globally unique ID required"))
            else:
                event_ids.add(event_id)
                candidate_by_id[event_id] = candidate
                state_by_event[event_id] = state
            for field in ("trajectory_id", "action"):
                if not isinstance(candidate.get(field), str) or not candidate[field].strip():
                    gaps.append(JoinGap("candidate_text", f"{location}.{field}", "nonempty text required"))
            for field in ("executor_receipt_sha256", "state_after_sha256"):
                if not isinstance(candidate.get(field), str) or not SHA256_RE.fullmatch(candidate[field]):
                    gaps.append(JoinGap("candidate_hash", f"{location}.{field}", "lowercase SHA-256 required"))
            disposition = candidate.get("execution_disposition")
            executions = candidate.get("lean_tactic_executions")
            if ((disposition == "executed" and executions != 1)
                    or (disposition == "parse_rejected" and executions != 0)
                    or disposition not in {"executed", "parse_rejected"}):
                gaps.append(JoinGap("execution_cost", location, "invalid disposition/cost"))
        if sorted(mapped) != list(range(raw_count)):
            gaps.append(JoinGap(
                "raw_sample_partition", f"{state_location}.candidates",
                f"must partition every raw sample 0..{raw_count - 1} exactly once",
            ))
    if state_request_ids != set(policy_receipts):
        gaps.append(JoinGap(
            "state_request_partition", "states[*].generation_request_id",
            "states must cover every policy request exactly once",
        ))
    selected = facts.get("selected_event_ids")
    if (not isinstance(selected, list) or not selected
            or any(type(item) is not str or item not in event_ids for item in selected)):
        gaps.append(JoinGap("selected_path", "selected_event_ids", "nonempty ordered event path required"))
    else:
        previous_after = initial_state_sha256
        for index, event_id in enumerate(selected):
            candidate = candidate_by_id[event_id]
            state = state_by_event[event_id]
            if (state.get("state_sha256") != previous_after
                    or candidate.get("depth") != index
                    or candidate.get("parent_event_id") != (selected[index - 1] if index else None)):
                gaps.append(JoinGap("selected_path", f"selected_event_ids[{index}]", "state/parent/depth chain mismatch"))
            previous_after = candidate.get("state_after_sha256")
        if candidate_by_id[selected[-1]].get("verifier_status") != "verified_proof":
            gaps.append(JoinGap("selected_path", "selected_event_ids[-1]", "terminal must be verified_proof"))
    receipt_hash = facts.get("receipt_sha256")
    if receipt_hash != _canonical_sha256({key: value for key, value in facts.items() if key != "receipt_sha256"}):
        gaps.append(JoinGap("candidate_facts_hash", "receipt_sha256", "canonical payload hash mismatch"))
    return gaps


def _load_private_key(path: Path, *, workspace_root: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    resolved = path.resolve()
    try:
        resolved.relative_to(workspace_root.resolve())
    except ValueError:
        pass
    else:
        raise JoinError("ephemeral signing key must be outside the workspace")
    if not resolved.is_file():
        raise JoinError("ephemeral signing key is missing")
    if os.name != "nt" and resolved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise JoinError("ephemeral signing key permissions must deny group/other access")
    key_bytes = resolved.read_bytes()
    try:
        if len(key_bytes) == 32:
            return Ed25519PrivateKey.from_private_bytes(key_bytes)
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except (TypeError, ValueError) as exc:
        raise JoinError("ephemeral signing key is not a readable Ed25519 key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise JoinError("ephemeral signing key is not Ed25519")
    return key


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    if path.exists() or temporary.exists():
        raise JoinError(f"refusing to overwrite output: {path}")
    temporary.write_bytes(_canonical_bytes(value) + b"\n")
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _assert_producer_evidence_unchanged(
    paths: Mapping[str, Any], audit: Mapping[str, Any], candidate_facts: Path,
) -> None:
    current_inputs = {
        "sessions_sha256": sha256_file(paths["sessions"]),
        "observer_sha256": sha256_file(paths["observer"]),
        "raw_tree_sha256": sha256_file(paths["raw_tree"]),
        "policy_receipts_sha256": {
            str(_load_json(path).get("request_id")): sha256_file(path)
            for path in paths["policies"]
        },
        "result_sha256": sha256_file(paths["result"]),
    }
    immutable_inputs = audit.get("immutable_inputs")
    if not isinstance(immutable_inputs, Mapping) or any(
        current_inputs[name] != immutable_inputs.get(name)
        for name in current_inputs
    ) or sha256_file(candidate_facts) != audit.get("candidate_facts_sha256"):
        raise JoinError("producer evidence changed before private-key access")


def run_join(args: argparse.Namespace) -> dict[str, Any]:
    """Join, attest, strict-replay, and convert one bounded smoke session."""
    candidate_facts = Path(args.candidate_facts)
    audit = audit_run(Path(args.run_root), candidate_facts=candidate_facts)
    if audit["gaps"]:
        raise JoinError("candidate facts are incomplete; run audit for the exact gap report")

    paths = _run_paths(Path(args.run_root))
    policy_values = [_load_json(path) for path in paths["policies"]]
    policy_candidates = policy_values[0].get("candidates") if policy_values else None
    if not isinstance(policy_candidates, list):
        raise JoinError("bound policy receipt candidates disappeared after audit")
    try:
        facts = _rebuild_bound_candidate_facts(
            paths, session_id=str(audit["session_id"]), raw_count=len(policy_candidates)
        )
    except CandidateFactsError as exc:
        raise JoinError(f"candidate facts reconstruction failed after audit: {exc}") from exc
    if _load_json(candidate_facts) != facts:
        raise JoinError("candidate facts changed or differ from producer-artifact reconstruction")

    # Lazy imports keep structural audits lightweight and ensure no model is
    # downloaded.  The tokenizer must already exist at the explicit local path.
    from transformers import AutoTokenizer
    from policy_service_bridge import CandidateObservation, receipt_to_search_state
    from policy_service_bridge.receipts import load_committed_receipt
    from shared_actor_bridge import (
        BehaviorIdentity,
        GenerationParameters,
        build_unsigned_envelope,
        sign_execution_attestation,
        to_ce_receipt,
        to_online_v2_receipts,
        validate_unsigned_envelope,
    )

    policy_by_request = {
        str(value["request_id"]): (path, load_committed_receipt(path))
        for path, value in zip(paths["policies"], policy_values, strict=True)
    }
    policy = next(iter(policy_by_request.values()))[1]
    session = _load_jsonl(paths["sessions"])[0]
    budget_path = Path(args.budget_config)
    if not budget_path.is_file():
        raise JoinError("budget config is missing")
    budget = _load_json(budget_path)
    if not isinstance(budget, dict):
        raise JoinError("budget config must be an object")
    tokenizer = AutoTokenizer.from_pretrained(
        str(Path(args.tokenizer_dir).resolve()), local_files_only=True, trust_remote_code=False
    )
    states = []
    for state_facts in facts["states"]:
        request_id = state_facts["generation_request_id"]
        if request_id not in policy_by_request:
            raise JoinError(f"candidate facts reference missing policy request {request_id}")
        policy_path, _state_policy = policy_by_request[request_id]
        observations = tuple(CandidateObservation(
            event_id=item["event_id"], trajectory_id=item["trajectory_id"], action=item["action"],
            sample_indices=tuple(item["sample_indices"]),
            sample_tactic_token_spans=tuple(tuple(span) for span in item["sample_tactic_token_spans"]),
            survivor_sample_index=item["survivor_sample_index"], action_value=item["action_value"],
            verifier_status=item["verifier_status"],
            executor_receipt_sha256=item["executor_receipt_sha256"],
            state_after_sha256=item["state_after_sha256"], depth=item["depth"],
            parent_event_id=item["parent_event_id"], execution_disposition=item["execution_disposition"],
            lean_tactic_executions=item["lean_tactic_executions"],
        ) for item in state_facts["candidates"])
        states.append(receipt_to_search_state(
            policy_path, state_id=state_facts["state_id"],
            state_sha256=state_facts["state_sha256"], observations=observations,
        ))
    generation_values = dict(policy["generation_contract"])
    generation_values.pop("generation_params_sha256")
    frozen_generation = budget.get("generation")
    if not isinstance(frozen_generation, dict):
        raise JoinError("budget config lacks a generation contract")
    for key in ("num_return_sequences", "max_new_tokens"):
        if generation_values.get(key) != frozen_generation.get(key):
            raise JoinError(f"policy generation {key} differs from frozen budget")
    envelope = build_unsigned_envelope(
        problem_id=session["source_id"], statement_sha256=session["source_sha256"],
        wave_index=args.wave_index, initial_state_sha256=facts["initial_state_sha256"],
        outcome="proof", actor_config_sha256=policy["actor_config_sha256"],
        budget_config_sha256=sha256_file(budget_path),
        tokenizer_lock_sha256=policy["tokenizer_lock_sha256"],
        identity=BehaviorIdentity(**policy["behavior_identity"]),
        generation=GenerationParameters(**generation_values), states=tuple(states),
        selected_event_ids=tuple(facts["selected_event_ids"]), tokenizer=tokenizer,
        eos_token_id=int(tokenizer.eos_token_id),
        receipt_id=(f"receipt-{session['session_id']}-"
                    f"{_canonical_sha256(facts['policy_receipts'])[:16]}"),
        attempt_id=f"attempt-{session['session_id']}-{sha256_file(paths['observer'])[:16]}",
    )
    validate_unsigned_envelope(envelope, tokenizer=tokenizer)

    # Recheck every producer artifact consumed by reconstruction immediately
    # before key access.  The envelope uses the in-memory reconstruction, never
    # fields copied from the untrusted sidecar.
    _assert_producer_evidence_unchanged(paths, audit, candidate_facts)

    private_key = _load_private_key(Path(args.private_key), workspace_root=Path(args.workspace_root))
    lock = _load_json(Path(args.verifier_lock))
    if not isinstance(lock, dict):
        raise JoinError("verifier lock must be an object")
    from cryptography.hazmat.primitives import serialization
    public_hex = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    if public_hex != lock.get("ed25519_public_key_hex"):
        raise JoinError("ephemeral signing key does not match the verifier lock")
    attested = sign_execution_attestation(
        envelope, tokenizer=tokenizer, attester_id=lock.get("verifier_id", ""),
        attester_lock_sha256=sha256_file(Path(args.verifier_lock)), private_key=private_key,
    )

    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    actor_path = output / "execution-attested-actor.json"
    _atomic_json(actor_path, attested)
    services_path = output / "service_receipts.jsonl"
    services_record = {
        "schema_version": "fate.reap.joined_service_receipt.v2",
        "session_id": session["session_id"],
        "policy_requests": [{
            "request_id": request_id,
            "policy_receipt_sha256": sha256_file(path),
            "generation_receipt_sha256": value["generation_receipt_sha256"],
        } for request_id, (path, value) in sorted(policy_by_request.items())],
    }
    services_path.write_bytes(_canonical_bytes(services_record) + b"\n")
    replay_dir = output / "strict-replay"
    replay_code, signed_path = run_strict_replay(ReplayInputs(
        problems=Path(args.problems), expected_problems_sha256=args.problems_sha256,
        source_id=session["source_id"], result_json=paths["result"],
        raw_tree_json=paths["raw_tree"], service_receipts_jsonl=services_path,
        actor_receipt_json=actor_path, generated_sha256=session["generated_sha256"],
        model_sha256=session["model_sha256"], runtime_receipt_sha256=args.runtime_receipt_sha256,
        expected_course_manifest_sha256=args.course_manifest_sha256,
        expected_lake_sha256=args.lake_sha256, verifier_lock=Path(args.verifier_lock),
        expected_verifier_lock_sha256=sha256_file(Path(args.verifier_lock)),
        verifier_private_key=Path(args.private_key), workspace_root=Path(args.workspace_root),
        course_project=Path(args.course_project), output_dir=replay_dir,
        lake=args.lake, timeout_seconds=args.timeout_seconds,
    ))
    if replay_code != 0:
        raise JoinError(f"strict replay rejected the proof; inspect {signed_path}")
    signed = _load_json(signed_path)
    converter_args = {
        "verifier_public_key_hex": public_hex,
        "expected_verifier_id": lock["verifier_id"],
        "expected_verifier_lock_sha256": sha256_file(Path(args.verifier_lock)),
        "tokenizer": tokenizer,
    }
    ce = to_ce_receipt(signed, **converter_args)
    searches, verifiers, paths_out = to_online_v2_receipts(signed, **converter_args)
    ce_path = output / "ce-receipt.json"
    _atomic_json(ce_path, ce)
    online_path = output / "online-v2-receipts.json"
    _atomic_json(online_path, {
        "schema_version": "fate.online_v2.converted_receipts.v1",
        "source_receipt_sha256": signed["receipt_sha256"],
        "searches": [asdict(value) for value in searches],
        "verifiers": [asdict(value) for value in verifiers],
        "paths": [asdict(value) for value in paths_out],
    })
    return {
        **audit,
        "status": "complete",
        "private_key_read": True,
        "lean_invoked": True,
        "signed": True,
        "converted": True,
        "outputs": {
            "signed_receipt": str(signed_path),
            "signed_receipt_sha256": sha256_file(signed_path),
            "ce_receipt": str(ce_path),
            "online_v2_receipts": str(online_path),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--candidate-facts", type=Path)
    parser.add_argument("--gap-report", type=Path)
    parser.add_argument("--execute", action="store_true", help="sign, strict-replay, and convert")
    parser.add_argument("--tokenizer-dir", type=Path)
    parser.add_argument("--budget-config", type=Path)
    parser.add_argument("--wave-index", type=int, default=1)
    parser.add_argument("--private-key", type=Path)
    parser.add_argument("--verifier-lock", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--problems", type=Path)
    parser.add_argument("--problems-sha256")
    parser.add_argument("--course-project", type=Path)
    parser.add_argument("--course-manifest-sha256")
    parser.add_argument("--lake", default="lake")
    parser.add_argument("--lake-sha256")
    parser.add_argument("--runtime-receipt-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.execute:
        try:
            report = audit_run(args.run_root, candidate_facts=args.candidate_facts)
        except JoinError as exc:
            parser.exit(2, f"fail closed: {exc}\n")
        if args.gap_report is not None:
            _atomic_json(args.gap_report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ready" else 2
    required = (
        "candidate_facts", "tokenizer_dir", "budget_config", "private_key", "verifier_lock",
        "workspace_root", "problems", "problems_sha256", "course_project",
        "course_manifest_sha256", "lake_sha256", "runtime_receipt_sha256", "output_dir",
    )
    missing = [name for name in required if getattr(args, name) in (None, "")]
    if missing:
        parser.error("--execute requires: " + ", ".join("--" + name.replace("_", "-") for name in missing))
    try:
        report = run_join(args)
    except JoinError as exc:
        parser.exit(2, f"fail closed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

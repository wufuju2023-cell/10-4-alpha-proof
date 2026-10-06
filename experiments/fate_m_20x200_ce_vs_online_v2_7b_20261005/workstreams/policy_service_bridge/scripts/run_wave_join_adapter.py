#!/usr/bin/env python3
"""Fail-closed 20-session candidate-facts, join, replay, and conversion adapter.

The actor directory is immutable input.  All sidecars and joined learner inputs
are written below a new output root, and every producer/pin file hash is checked
again before the wave receives a terminal DONE marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import time
from typing import Any, Iterable


HERE = Path(__file__).resolve()
WORKSTREAM_ROOT = HERE.parents[2]
sys.path[:0] = [
    str(WORKSTREAM_ROOT / "lean_integration" / "src"),
    str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"),
    str(WORKSTREAM_ROOT / "policy_service_bridge" / "src"),
    str(WORKSTREAM_ROOT / "online_v2_arm" / "src"),
    str(HERE.parent),
]

from assemble_wave_join_inputs import assemble  # noqa: E402
from fate_reap.candidate_facts import collect_candidate_facts_envelope  # noqa: E402
from fate_reap.e2e_receipt_join import run_join  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite output: {path}")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                indent=2, allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON: {path}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"unreadable JSONL: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL record at {path}:{line_number}")
        records.append(value)
    return records


def required_session_paths(root: Path) -> tuple[Path, ...]:
    policies = tuple(sorted(root.glob("actor_receipts/*/policy_requests/*.json")))
    if not policies:
        raise ValueError(f"{root.name}: no committed policy receipts")
    paths = (
        root / "report.json",
        root / "DONE.json",
        root / "sessions.jsonl",
        root / "observer.jsonl",
        root / "raw_tree.json",
        root / "session" / "result.json",
        *policies,
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing actor evidence: " + ", ".join(missing))
    return paths


def snapshot(paths: Iterable[Path], *, relative_to: Path | None = None) -> dict[str, str]:
    records: dict[str, str] = {}
    for path in paths:
        resolved = path.resolve()
        label = (str(resolved.relative_to(relative_to.resolve()))
                 if relative_to is not None else str(resolved))
        records[label] = sha256_file(resolved)
    return records


def tree_id(observer: Path) -> str:
    canonical_kinds = {
        "canonical_candidate", "canonical_candidate_result", "canonical_selected_path"
    }
    values = {
        record.get("tree_id") for record in load_jsonl(observer)
        if record.get("kind") in canonical_kinds
    }
    if len(values) != 1 or not isinstance(next(iter(values), None), str):
        raise ValueError(f"{observer}: expected exactly one canonical tree_id")
    value = next(iter(values))
    if not value:
        raise ValueError(f"{observer}: canonical tree_id is empty")
    return value


def load_plan(path: Path, *, wave_index: int) -> tuple[dict[str, Any], list[str]]:
    value = load_json(path)
    tasks = value.get("tasks") if isinstance(value, dict) else None
    if not isinstance(tasks, list) or len(tasks) != 20:
        raise ValueError("wave plan must contain exactly 20 tasks")
    sessions = [task.get("session_id") if isinstance(task, dict) else None for task in tasks]
    if any(not isinstance(session, str) or not session for session in sessions):
        raise ValueError("wave plan contains an invalid session_id")
    if len(set(sessions)) != 20:
        raise ValueError("wave plan session_ids are not unique")
    expected_suffix = f"_v{wave_index:03d}"
    if any(not session.endswith(expected_suffix) for session in sessions):
        raise ValueError("wave plan session_id does not match --wave-index")
    return value, sessions


def validate_preflight(args: argparse.Namespace) -> tuple[list[str], dict[str, Any], dict[str, str]]:
    if isinstance(args.wave_index, bool) or not 1 <= args.wave_index <= 200:
        raise ValueError("wave-index must be in 1..200")
    if args.output_root.exists():
        raise FileExistsError(f"output root already exists: {args.output_root}")
    if not args.actor_root.is_dir():
        raise FileNotFoundError(f"actor root is missing: {args.actor_root}")
    if not args.workspace_root.is_dir() or not args.course_project.is_dir():
        raise FileNotFoundError("workspace-root and course-project must be existing directories")
    if not args.tokenizer_dir.is_dir():
        raise FileNotFoundError(f"tokenizer directory is missing: {args.tokenizer_dir}")
    if not args.private_key.is_file():
        raise FileNotFoundError(f"verifier private key is missing: {args.private_key}")
    if args.course_manifest.resolve() != (args.course_project / "lake-manifest.json").resolve():
        raise ValueError("course-manifest must be COURSE_PROJECT/lake-manifest.json")
    pinned_files = {
        "plan": args.plan,
        "problems": args.problems,
        "budget_config": args.budget_config,
        "runtime_receipt": args.runtime_receipt,
        "course_manifest": args.course_manifest,
        "lake": args.lake,
        "verifier_lock": args.verifier_lock,
    }
    missing = [f"{name}={path}" for name, path in pinned_files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing frozen pin files: " + ", ".join(missing))
    plan, sessions = load_plan(args.plan, wave_index=args.wave_index)
    expected_problem_hash = plan.get("source", {}).get("problems_sha256")
    if not isinstance(expected_problem_hash, str) or sha256_file(args.problems) != expected_problem_hash:
        raise ValueError("problems file does not match the wave plan pin")
    actor_done = load_json(args.actor_root / "DONE.json")
    if (not isinstance(actor_done, dict) or actor_done.get("state") != "DONE"
            or actor_done.get("sessions") != 20):
        raise ValueError("actor root is not terminal DONE with exactly 20 sessions")
    actor_evidence: dict[str, str] = snapshot(
        (args.actor_root / "DONE.json",), relative_to=args.actor_root
    )
    for session_id in sessions:
        root = args.actor_root / session_id
        if not root.is_dir():
            raise FileNotFoundError(f"missing actor session: {root}")
        actor_evidence.update(snapshot(required_session_paths(root), relative_to=args.actor_root))
    pin_hashes = {name: sha256_file(path) for name, path in pinned_files.items()}
    return sessions, {"plan": plan, "actor_evidence": actor_evidence}, pin_hashes


def run(args: argparse.Namespace) -> dict[str, Any]:
    sessions, preflight, pin_hashes = validate_preflight(args)
    args.output_root.mkdir(parents=True)
    source_manifest = {
        "schema_version": "fate.wave_join.source_evidence.v1",
        "wave_index": args.wave_index,
        "actor_root": str(args.actor_root.resolve()),
        "actor_evidence_sha256": preflight["actor_evidence"],
        "pin_sha256": pin_hashes,
    }
    source_manifest_path = args.output_root / "source-evidence-manifest.json"
    publish_json(source_manifest_path, source_manifest)
    started = time.monotonic()
    completed = 0
    try:
        for session_id in sessions:
            session_root = args.actor_root / session_id
            source_paths = required_session_paths(session_root)
            expected_source = snapshot(source_paths, relative_to=args.actor_root)
            if any(pin_hashes[name] != sha256_file(path) for name, path in {
                "plan": args.plan, "problems": args.problems,
                "budget_config": args.budget_config,
                "runtime_receipt": args.runtime_receipt,
                "course_manifest": args.course_manifest, "lake": args.lake,
                "verifier_lock": args.verifier_lock,
            }.items()):
                raise ValueError("frozen pin changed during wave join")
            policies = tuple(sorted(session_root.glob("actor_receipts/*/policy_requests/*.json")))
            facts_path = args.output_root / "candidate-facts" / f"{session_id}.json"
            facts_path.parent.mkdir(parents=True, exist_ok=True)
            collect_candidate_facts_envelope(
                observer=session_root / "observer.jsonl",
                raw_tree=session_root / "raw_tree.json",
                result_json=session_root / "session" / "result.json",
                actor_receipts=policies,
                output=facts_path,
                session_id=session_id,
                tree_id=tree_id(session_root / "observer.jsonl"),
                expected_raw_count=64,
            )
            join_root = args.output_root / "joins" / session_id
            report = run_join(SimpleNamespace(
                run_root=session_root,
                candidate_facts=facts_path,
                tokenizer_dir=args.tokenizer_dir,
                budget_config=args.budget_config,
                wave_index=args.wave_index,
                private_key=args.private_key,
                verifier_lock=args.verifier_lock,
                workspace_root=args.workspace_root,
                problems=args.problems,
                problems_sha256=pin_hashes["problems"],
                course_project=args.course_project,
                course_manifest_sha256=pin_hashes["course_manifest"],
                lake=str(args.lake),
                lake_sha256=pin_hashes["lake"],
                runtime_receipt_sha256=pin_hashes["runtime_receipt"],
                timeout_seconds=args.timeout_seconds,
                output_dir=join_root,
            ))
            if report.get("status") != "complete" or not all(
                report.get(field) is True
                for field in ("private_key_read", "lean_invoked", "signed", "converted")
            ):
                raise ValueError(f"{session_id}: hardened join did not complete")
            if snapshot(source_paths, relative_to=args.actor_root) != expected_source:
                raise ValueError(f"{session_id}: actor evidence changed during join")
            publish_json(args.output_root / "join-reports" / f"{session_id}.json", report)
            completed += 1
            elapsed = time.monotonic() - started
            print(json.dumps({
                "event": "wave_join_session_complete", "session_id": session_id,
                "completed": completed, "total": 20,
                "elapsed_seconds": round(elapsed, 1),
                "sessions_per_minute": round(60.0 * completed / max(elapsed, 0.001), 3),
            }, sort_keys=True), flush=True)

        current_actor = snapshot((args.actor_root / "DONE.json",), relative_to=args.actor_root)
        for session_id in sessions:
            current_actor.update(snapshot(
                required_session_paths(args.actor_root / session_id), relative_to=args.actor_root
            ))
        if current_actor != preflight["actor_evidence"]:
            raise ValueError("actor evidence changed across the wave join")
        current_pins = {
            "plan": sha256_file(args.plan), "problems": sha256_file(args.problems),
            "budget_config": sha256_file(args.budget_config),
            "runtime_receipt": sha256_file(args.runtime_receipt),
            "course_manifest": sha256_file(args.course_manifest),
            "lake": sha256_file(args.lake), "verifier_lock": sha256_file(args.verifier_lock),
        }
        if current_pins != pin_hashes:
            raise ValueError("frozen pin changed across the wave join")
        training_dir = args.output_root / "training-inputs"
        manifest = assemble(
            plan_path=args.plan, join_root=args.output_root / "joins",
            output_dir=training_dir, wave_index=args.wave_index,
        )
        done = {
            "schema_version": "fate.wave_join.done.v1", "state": "DONE",
            "wave_index": args.wave_index, "sessions": completed,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "source_evidence_manifest_sha256": sha256_file(source_manifest_path),
            "training_inputs_manifest_sha256": sha256_file(training_dir / "manifest.json"),
            "ce_receipts_sha256": manifest["ce_receipts"]["sha256"],
            "online_receipt_pins_sha256": manifest["online_receipt_pins_file"]["sha256"],
        }
        publish_json(args.output_root / "DONE.json", done)
        return done
    except Exception as exc:
        try:
            publish_json(args.output_root / "FAILED.json", {
                "schema_version": "fate.wave_join.failed.v1", "state": "FAILED",
                "wave_index": args.wave_index, "completed_sessions": completed,
                "error_type": type(exc).__name__, "error": str(exc),
            })
        except Exception:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--budget-config", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--course-project", type=Path, required=True)
    parser.add_argument("--course-manifest", type=Path, required=True)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--verifier-lock", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        done = run(args)
    except Exception as exc:
        print(f"fail closed: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps({"event": "wave_join_complete", **done}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

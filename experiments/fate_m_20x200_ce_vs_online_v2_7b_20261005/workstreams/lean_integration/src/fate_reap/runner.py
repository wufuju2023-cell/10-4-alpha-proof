"""Run isolated real Lean/Reap sessions and enforce two-process admission."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .checkpoint import audit_checkpoints, write_policy_state
from .evidence import Evidence, classify_evidence, sha256_file
from .strict_replay import ReplayInputs, run_strict_replay


CONTROL_ENV = {
    "REAP_OBSERVER_PATH", "REAP_CHECKPOINT_DIR", "REAP_TREE_ID",
    "REAP_POLICY_VERSION", "REAP_CHECKPOINT_TIMEOUT_SECONDS",
}


@dataclass(frozen=True)
class Session:
    session_id: str
    theorem_file: Path
    policy_base_url: str
    value_base_url: str
    source_sha256: str
    generated_sha256: str
    problems_sha256: str
    model_sha256: str

    @property
    def tree_id(self) -> str:
        return f"{self.session_id}-{self.generated_sha256[:12]}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_manifest(path: Path, theorem_root: Path, expected_problems_sha256: str) -> list[Session]:
    sessions: list[Session] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or record.get("schema_version") != "fate.reap.session.v1":
            raise ValueError(f"{path}:{number}: unsupported schema")
        session_id = record.get("session_id")
        if type(session_id) is not str or session_id in seen or not session_id.replace("_", "").isalnum():
            raise ValueError(f"{path}:{number}: invalid or duplicate session_id")
        theorem_value = record.get("theorem_file")
        if type(theorem_value) is not str:
            raise ValueError(f"{path}:{number}: theorem_file must be a string")
        theorem_file = Path(theorem_value).resolve()
        if not _inside(theorem_file, theorem_root) or theorem_file.suffix != ".lean":
            raise ValueError(f"{path}:{number}: theorem escapes theorem root")
        actual = sha256_file(theorem_file)
        generated = record.get("generated_sha256")
        source = record.get("source_sha256")
        problems = record.get("problems_sha256")
        model = record.get("model_sha256")
        if any(type(value) is not str for value in (generated, source, problems, model)):
            raise ValueError(f"{path}:{number}: hashes must be strings")
        if actual != generated:
            raise ValueError(f"{path}:{number}: generated theorem hash mismatch")
        if problems != expected_problems_sha256:
            raise ValueError(f"{path}:{number}: top-level problems hash mismatch")
        policy = record.get("policy_base_url")
        value = record.get("value_base_url")
        if type(policy) is not str or type(value) is not str:
            raise ValueError(f"{path}:{number}: endpoint must be a string")
        sessions.append(Session(session_id, theorem_file, policy.rstrip("/"), value.rstrip("/"),
                                source, generated, problems, model))
        seen.add(session_id)
    if not sessions:
        raise ValueError("manifest contains no sessions")
    return sessions


def build_session_env(
    base: dict[str, str],
    session: Session,
    session_dir: Path,
    *,
    observer_mode: str,
    initial_policy_version: int,
    checkpoint_timeout_seconds: int,
) -> dict[str, str]:
    if observer_mode not in {"record", "checkpoint"}:
        raise ValueError("observer_mode must be record or checkpoint")
    env = dict(base)
    for name in CONTROL_ENV:
        env.pop(name, None)
    observer_path = session_dir / "observer.jsonl"
    endpoint_values = {"session_id": session.session_id, "tree_id": session.tree_id}
    policy_endpoint = session.policy_base_url.format(**endpoint_values)
    value_endpoint = session.value_base_url.format(**endpoint_values)
    env.update({
        "REAP_SESSION_ID": session.session_id,
        "REAP_SESSION_DIR": str(session_dir),
        "REAP_POLICY_ENDPOINT": policy_endpoint,
        "REAP_VALUE_ENDPOINT": value_endpoint,
        "REAP_PS_ENDPOINT": "",
        "REAP_OBSERVER_PATH": str(observer_path),
        "REAP_TREE_ID": session.tree_id,
        "REAP_POLICY_VERSION": str(initial_policy_version),
        "FATE_SERVICE_RECEIPT_PATH": str(session_dir / "service_requests.jsonl"),
        "FATE_ACTOR_RECEIPT_PATH": str(session_dir / "actor_receipt.json"),
    })
    if observer_mode == "checkpoint":
        checkpoint_dir = session_dir / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        env["REAP_CHECKPOINT_DIR"] = str(checkpoint_dir)
        env["REAP_CHECKPOINT_TIMEOUT_SECONDS"] = str(checkpoint_timeout_seconds)
    return env


async def _terminate(process: asyncio.subprocess.Process) -> str:
    if process.returncode is not None:
        return "already_exited"
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        return "taskkill_tree_force"
    try:
        os.killpg(process.pid, signal.SIGTERM)
        return "sigterm_process_group"
    except ProcessLookupError:
        return "process_group_already_exited"


def _candidate_solved(result_path: Path, session_id: str) -> bool:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(result, dict) and result.get("schema_version") == "reap.training.result.v1"
        and type(result.get("session_id")) is str and result["session_id"] == session_id
        and type(result.get("solved")) is bool and result["solved"]
        and type(result.get("status")) is str and result["status"] == "solved"
        and type(result.get("proof_script")) is str and result["proof_script"].strip()
    )


async def run_one(
    session: Session,
    *,
    project_dir: Path,
    course_project: Path,
    problems: Path,
    output_root: Path,
    timeout_seconds: float,
    strict_timeout_seconds: float,
    heartbeat_seconds: float,
    lake: str,
    strict_lake: str,
    observer_mode: str,
    initial_policy_version: int,
    checkpoint_timeout_seconds: int,
    semaphore: asyncio.Semaphore,
    runtime_receipt_sha256: str,
    course_manifest_sha256: str,
    strict_lake_sha256: str,
    verifier_lock: Path,
    verifier_lock_sha256: str,
    verifier_id: str,
    verifier_public_key_hex: str,
    verifier_private_key: Path,
    workspace_root: Path,
) -> dict:
    async with semaphore:
        session_dir = output_root / session.session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        input_dir = session_dir / "input"
        input_dir.mkdir()
        pinned_theorem = input_dir / f"{session.generated_sha256}.lean"
        shutil.copyfile(session.theorem_file, pinned_theorem)
        if sha256_file(pinned_theorem) != session.generated_sha256:
            raise RuntimeError("content-addressed theorem copy mismatch")
        receipt: dict[str, object] = {
            "schema_version": "fate.reap.process.v2", "session_id": session.session_id,
            "tree_id": session.tree_id, "theorem_file": str(pinned_theorem),
            "source_sha256": session.source_sha256, "generated_sha256": session.generated_sha256,
            "problems_sha256": session.problems_sha256, "observer_mode": observer_mode,
            "model_sha256": session.model_sha256,
            "runtime_receipt_sha256": runtime_receipt_sha256,
            "course_manifest_sha256": course_manifest_sha256,
            "strict_lake_sha256": strict_lake_sha256,
        }
        (session_dir / "session.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        env = build_session_env(
            os.environ, session, session_dir, observer_mode=observer_mode,
            initial_policy_version=initial_policy_version,
            checkpoint_timeout_seconds=checkpoint_timeout_seconds,
        )
        write_policy_state(session_dir, session_id=session.session_id, tree_id=session.tree_id,
                           step=0, policy_version=initial_policy_version)
        options: dict[str, object] = {"cwd": str(project_dir), "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        started = time.monotonic()
        termination = "natural"
        with (session_dir / "stdout.log").open("wb") as stdout, (session_dir / "stderr.log").open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                lake, "env", "lean", str(pinned_theorem), stdout=stdout, stderr=stderr, **options
            )
            timed_out = False
            while process.returncode is None:
                elapsed = time.monotonic() - started
                remaining = timeout_seconds - elapsed
                if remaining <= 0:
                    timed_out = True
                    termination = await _terminate(process)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        termination += "+kill"
                        await process.wait()
                    break
                try:
                    await asyncio.wait_for(process.wait(), timeout=min(heartbeat_seconds, remaining))
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - started
                    print(json.dumps({
                        "event": "heartbeat", "session_id": session.session_id,
                        "elapsed_seconds": round(elapsed, 1),
                        "remaining_seconds": round(max(0.0, timeout_seconds - elapsed), 1),
                    }), flush=True)
        returncode = int(process.returncode if process.returncode is not None else 124)
        replay_receipt: Path | None = None
        if not timed_out and returncode == 0 and _candidate_solved(session_dir / "result.json", session.session_id):
            try:
                _, replay_receipt = await asyncio.to_thread(
                    run_strict_replay,
                    ReplayInputs(
                        problems=problems, expected_problems_sha256=session.problems_sha256,
                        source_id=session.session_id, result_json=session_dir / "result.json",
                        raw_tree_json=session_dir / "raw_tree.json",
                        service_receipts_jsonl=session_dir / "service_requests.jsonl",
                        actor_receipt_json=session_dir / "actor_receipt.json",
                        generated_sha256=session.generated_sha256,
                        model_sha256=session.model_sha256,
                        runtime_receipt_sha256=runtime_receipt_sha256,
                        expected_course_manifest_sha256=course_manifest_sha256,
                        expected_lake_sha256=strict_lake_sha256,
                        verifier_lock=verifier_lock,
                        expected_verifier_lock_sha256=verifier_lock_sha256,
                        verifier_private_key=verifier_private_key,
                        workspace_root=workspace_root,
                        course_project=course_project,
                        output_dir=session_dir / "strict_replay", lake=strict_lake,
                        timeout_seconds=strict_timeout_seconds,
                    ),
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                (session_dir / "strict_replay_failure.json").write_text(
                    json.dumps({"error_type": type(exc).__name__, "message": str(exc)}), encoding="utf-8"
                )
        evidence: Evidence = classify_evidence(
            session.session_id, session_dir, returncode=returncode, timed_out=timed_out,
            source_sha256=session.source_sha256, generated_sha256=session.generated_sha256,
            problems_sha256=session.problems_sha256, tree_id=session.tree_id,
            model_sha256=session.model_sha256,
            runtime_receipt_sha256=runtime_receipt_sha256,
            course_manifest_sha256=course_manifest_sha256,
            strict_lake_sha256=strict_lake_sha256,
            verifier_id=verifier_id, verifier_lock_sha256=verifier_lock_sha256,
            verifier_public_key_hex=verifier_public_key_hex,
            strict_replay_receipt=replay_receipt,
        )
        checkpoint_audit = None
        if observer_mode == "checkpoint":
            checkpoint_audit = audit_checkpoints(
                session_dir / "observer.jsonl", session_dir / "checkpoints",
                session_id=session.session_id, tree_id=session.tree_id,
                initial_policy_version=initial_policy_version,
            ).to_dict()
            if not checkpoint_audit["valid"]:
                evidence = Evidence(
                    session.session_id, "infrastructure_error", False, None, False,
                    evidence.status, f"checkpoint audit failed: {checkpoint_audit['reason']}",
                    evidence.service_audit, evidence.strict_replay_sha256,
                )
        if sha256_file(pinned_theorem) != session.generated_sha256:
            evidence = Evidence(session.session_id, "infrastructure_error", False, None, False,
                                evidence.status, "pinned theorem changed during execution")
        receipt.update({
            "returncode": returncode, "timed_out": timed_out, "termination": termination,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout_sha256": sha256_file(session_dir / "stdout.log"),
            "stderr_sha256": sha256_file(session_dir / "stderr.log"),
            "strict_replay_receipt_sha256": sha256_file(replay_receipt) if replay_receipt else None,
            "checkpoint_audit": checkpoint_audit, "evidence": evidence.to_dict(),
        })
        process_receipt = session_dir / "process_receipt.json"
        process_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"event": "session_complete", **receipt}, ensure_ascii=False), flush=True)
        return receipt


async def run_all(args: argparse.Namespace) -> list[dict]:
    project = args.project_dir.resolve()
    course = args.course_project.resolve()
    problems = args.problems.resolve()
    problems_hash = sha256_file(problems)
    if problems_hash != args.expected_problems_sha256.lower():
        raise ValueError("top-level problems.jsonl hash mismatch")
    runtime_receipt = args.runtime_receipt.resolve()
    if sha256_file(runtime_receipt) != args.expected_runtime_receipt_sha256.lower():
        raise ValueError("runtime receipt hash mismatch")
    runtime_receipt_sha256 = sha256_file(runtime_receipt)
    course_manifest = course / "lake-manifest.json"
    if sha256_file(course_manifest) != args.expected_course_manifest_sha256.lower():
        raise ValueError("course manifest hash mismatch")
    course_manifest_sha256 = sha256_file(course_manifest)
    verifier_lock = args.verifier_lock.resolve()
    verifier_lock_sha256 = sha256_file(verifier_lock)
    if verifier_lock_sha256 != args.expected_verifier_lock_sha256.lower():
        raise ValueError("trusted verifier lock hash mismatch")
    lock_value = json.loads(verifier_lock.read_text(encoding="utf-8"))
    if (not isinstance(lock_value, dict) or type(lock_value.get("verifier_id")) is not str
            or type(lock_value.get("ed25519_public_key_hex")) is not str):
        raise ValueError("trusted verifier lock lacks verifier id/public key")
    for command, expected, label in (
        (args.lake, args.expected_lake_sha256, "Reap lake"),
        (args.strict_lake, args.expected_strict_lake_sha256, "strict lake"),
    ):
        resolved = Path(shutil.which(command) or command).resolve()
        if not resolved.is_file() or sha256_file(resolved) != expected.lower():
            raise ValueError(f"{label} executable hash mismatch")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sessions = load_manifest(args.manifest.resolve(), args.theorem_root.resolve(), problems_hash)
    collisions = [s.session_id for s in sessions if (output / s.session_id).exists()]
    if collisions:
        raise FileExistsError(f"existing session outputs: {', '.join(collisions[:5])}")
    semaphore = asyncio.Semaphore(args.concurrency)
    return await asyncio.gather(*[
        run_one(
            session, project_dir=project, course_project=course, problems=problems,
            output_root=output, timeout_seconds=args.timeout_seconds,
            strict_timeout_seconds=args.strict_timeout_seconds,
            heartbeat_seconds=args.heartbeat_seconds, lake=args.lake,
            strict_lake=args.strict_lake, observer_mode=args.observer_mode,
            initial_policy_version=args.initial_policy_version,
            checkpoint_timeout_seconds=args.checkpoint_timeout_seconds, semaphore=semaphore,
            runtime_receipt_sha256=runtime_receipt_sha256,
            course_manifest_sha256=course_manifest_sha256,
            strict_lake_sha256=args.expected_strict_lake_sha256.lower(),
            verifier_lock=verifier_lock, verifier_lock_sha256=verifier_lock_sha256,
            verifier_id=lock_value["verifier_id"],
            verifier_public_key_hex=lock_value["ed25519_public_key_hex"],
            verifier_private_key=args.verifier_private_key.resolve(),
            workspace_root=args.workspace_root.resolve(),
        ) for session in sessions
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--theorem-root", type=Path, required=True)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--expected-problems-sha256", required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--course-project", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--expected-runtime-receipt-sha256", required=True)
    parser.add_argument("--expected-course-manifest-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--strict-timeout-seconds", type=float, default=120)
    parser.add_argument("--heartbeat-seconds", type=float, default=20)
    parser.add_argument("--lake", default="lake")
    parser.add_argument("--strict-lake", default="lake")
    parser.add_argument("--expected-lake-sha256", required=True)
    parser.add_argument("--expected-strict-lake-sha256", required=True)
    parser.add_argument("--verifier-lock", type=Path, required=True)
    parser.add_argument("--expected-verifier-lock-sha256", required=True)
    parser.add_argument("--verifier-private-key", type=Path, required=True,
                        help="Ed25519 private key file; must resolve outside --workspace-root")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--observer-mode", choices=("record", "checkpoint"), default="record")
    parser.add_argument("--initial-policy-version", type=int, default=0)
    parser.add_argument("--checkpoint-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if (args.concurrency < 1 or args.timeout_seconds <= 0 or args.strict_timeout_seconds <= 0
            or args.heartbeat_seconds <= 0 or args.initial_policy_version < 0
            or args.checkpoint_timeout_seconds <= 0):
        parser.error("concurrency, timeouts, and policy version are invalid")
    results = asyncio.run(run_all(args))
    summary_path = args.output_dir.resolve() / "summary.jsonl"
    summary_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in results),
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for item in results:
        key = item["evidence"]["classification"]
        counts[key] = counts.get(key, 0) + 1
    invalid = sum(counts.get(key, 0) for key in ("infrastructure_error", "indeterminate"))
    report = {"event": "run_complete", "sessions": len(results), "classifications": counts,
              "experiment_valid": invalid == 0}
    print(json.dumps(report), flush=True)
    return 0 if invalid == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

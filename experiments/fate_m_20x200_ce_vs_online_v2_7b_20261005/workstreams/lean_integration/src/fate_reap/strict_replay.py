"""Independently compile a Reap proof with fatal sorry diagnostics and provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical_receipt import (
    load_verifier_lock,
    object_hash,
    sign_actor_receipt,
    validate_unsigned_actor_receipt,
)
from .session_builder import SOURCE_BODY, load_records


FORBIDDEN = re.compile(r"(?<![A-Za-z0-9_])(sorry|admit)(?![A-Za-z0-9_])|\?[A-Za-z0-9_]*")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_forbidden_tokens(proof_script: str) -> list[str]:
    return sorted({match.group(0) for match in FORBIDDEN.finditer(proof_script)})


def materialize(formal_statement: str, proof_script: str) -> str:
    if formal_statement.count(SOURCE_BODY) != 1:
        raise ValueError("source theorem does not have one canonical sorry body")
    if not proof_script.strip():
        raise ValueError("empty proof script")
    if not formal_statement.startswith("import Mathlib\n"):
        raise ValueError("source theorem must start with import Mathlib")
    hardened = formal_statement.replace(
        "import Mathlib\n",
        "import Mathlib\n\n"
        "set_option warningAsError true\n"
        "set_option linter.unusedVariables false\n"
        "set_option linter.unusedSimpArgs false\n",
        1,
    )
    body = ":= by\n" + "\n".join("  " + line for line in proof_script.strip().splitlines())
    return hardened.replace(SOURCE_BODY, body, 1)


def parse_diagnostics(*streams: bytes) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for stream in streams:
        for raw_line in stream.decode("utf-8", errors="replace").splitlines():
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and ("severity" in value or "kind" in value):
                diagnostics.append(value)
    return diagnostics


def _run(command: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd=cwd, capture_output=True, check=False, timeout=timeout)


@dataclass(frozen=True)
class ReplayInputs:
    problems: Path
    expected_problems_sha256: str
    source_id: str
    result_json: Path
    raw_tree_json: Path
    service_receipts_jsonl: Path
    actor_receipt_json: Path
    generated_sha256: str
    model_sha256: str
    runtime_receipt_sha256: str
    expected_course_manifest_sha256: str
    expected_lake_sha256: str
    verifier_lock: Path
    expected_verifier_lock_sha256: str
    verifier_private_key: Path
    workspace_root: Path
    course_project: Path
    output_dir: Path
    lake: str = "lake"
    timeout_seconds: float = 120.0


def run_strict_replay(inputs: ReplayInputs) -> tuple[int, Path]:
    if inputs.timeout_seconds <= 0:
        raise ValueError("strict replay timeout must be positive")
    resolved_lake_text = shutil.which(inputs.lake) or inputs.lake
    resolved_lake = Path(resolved_lake_text).resolve()
    if not resolved_lake.is_file() or sha256_file(resolved_lake) != inputs.expected_lake_sha256:
        raise ValueError("strict lake executable hash mismatch")
    problems_hash = sha256_file(inputs.problems)
    if problems_hash != inputs.expected_problems_sha256:
        raise ValueError("top-level problems.jsonl hash mismatch")
    records = {str(record["id"]): record for record in load_records(inputs.problems)}
    if inputs.source_id not in records:
        raise ValueError(f"unknown source id: {inputs.source_id}")
    source = records[inputs.source_id]
    workspace_root = inputs.workspace_root.resolve()
    key_path = inputs.verifier_private_key.resolve()
    try:
        key_path.relative_to(workspace_root)
    except ValueError:
        pass
    else:
        raise ValueError("verifier private key must be outside the workspace")
    if not key_path.is_file():
        raise ValueError("verifier private key is missing")
    if os.name != "nt" and key_path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("verifier private key permissions must deny group/other access")
    result = json.loads(inputs.result_json.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Reap result must be an object")
    if (result.get("schema_version") != "reap.training.result.v1"
            or type(result.get("solved")) is not bool or not result["solved"]
            or type(result.get("status")) is not str or result["status"] != "solved"
            or type(result.get("session_id")) is not str or result["session_id"] != inputs.source_id
            or type(result.get("proof_script")) is not str or not result["proof_script"].strip()):
        raise ValueError("result is not a strictly typed solved Reap receipt")
    for required in (inputs.raw_tree_json, inputs.service_receipts_jsonl, inputs.actor_receipt_json):
        if not required.is_file():
            raise ValueError(f"missing replay input: {required}")

    try:
        actor_receipt = json.loads(inputs.actor_receipt_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("actor receipt is invalid JSON") from exc
    if not isinstance(actor_receipt, dict):
        raise ValueError("actor receipt must be an object")
    request_hash, path_hash, final_state_hash = validate_unsigned_actor_receipt(
        actor_receipt, problem_id=inputs.source_id, statement_sha256=str(source["sha256"]),
    )
    initial_state_hash = actor_receipt["request"]["initial_state_sha256"]

    toolchain_path = inputs.course_project / "lean-toolchain"
    manifest_path = inputs.course_project / "lake-manifest.json"
    toolchain = toolchain_path.read_text(encoding="utf-8").strip() if toolchain_path.is_file() else ""
    manifest_hash = sha256_file(manifest_path) if manifest_path.is_file() else ""
    if manifest_hash != inputs.expected_course_manifest_sha256:
        raise ValueError("course lake-manifest.json hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mathlib = next(package for package in manifest["packages"] if package.get("name") == "mathlib")
        mathlib_revision = mathlib["rev"]
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("course manifest does not pin a git mathlib revision") from exc
    verifier_lock = load_verifier_lock(
        inputs.verifier_lock,
        expected_sha256=inputs.expected_verifier_lock_sha256,
        runtime_receipt_sha256=inputs.runtime_receipt_sha256,
        lake_manifest_sha256=manifest_hash,
        lake_executable_sha256=inputs.expected_lake_sha256,
        timeout_seconds=inputs.timeout_seconds,
    )
    if verifier_lock["executor_source_sha256"] != sha256_file(Path(__file__)):
        raise ValueError("trusted verifier lock executor source hash mismatch")
    if verifier_lock["mathlib_revision"] != mathlib_revision:
        raise ValueError("trusted verifier lock mathlib revision mismatch")

    proof_script = result["proof_script"]
    forbidden = scan_forbidden_tokens(proof_script)
    proof = materialize(str(source["formal_statement"]), proof_script)
    inputs.output_dir.mkdir(parents=True, exist_ok=False)
    theorem = inputs.output_dir / f"{inputs.source_id}.lean"
    theorem.write_text(proof, encoding="utf-8", newline="\n")
    stdout_path = inputs.output_dir / "stdout.log"
    stderr_path = inputs.output_dir / "stderr.log"
    timed_out = False
    returncode: int | None = None
    stdout = stderr = b""
    lake_command = str(resolved_lake)
    command = [lake_command, "env", "lean", "--json", "-E", "hasSorry", str(theorem.resolve())]
    if not forbidden:
        try:
            completed = _run(command, inputs.course_project.resolve(), inputs.timeout_seconds)
            returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    diagnostics = parse_diagnostics(stdout, stderr)

    version = githash = ""
    try:
        version_run = _run([lake_command, "env", "lean", "--version"], inputs.course_project.resolve(), 15)
        hash_run = _run([lake_command, "env", "lean", "-g"], inputs.course_project.resolve(), 15)
        if version_run.returncode == 0:
            version = version_run.stdout.decode("utf-8", errors="replace").strip()
        if hash_run.returncode == 0:
            githash = hash_run.stdout.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        pass
    bad_diagnostic = any(
        item.get("kind") == "hasSorry" or item.get("severity") == "error" for item in diagnostics
    )
    strict_solved = bool(
        not forbidden and not timed_out and returncode == 0 and not bad_diagnostic
        and version == verifier_lock["lean_version"] and githash == verifier_lock["lean_git_hash"]
        and toolchain == "leanprover/lean4:v4.28.0" and re.fullmatch(r"[0-9a-f]{64}", manifest_hash)
    )
    kernel_receipt = {
        "schema_version": "fate.strict_replay.v2",
        "session_id": inputs.source_id,
        "source_id": inputs.source_id,
        "strict_solved": strict_solved,
        "returncode": returncode,
        "timed_out": timed_out,
        "problems_sha256": problems_hash,
        "source_sha256": str(source["sha256"]),
        "generated_sha256": inputs.generated_sha256,
        "model_sha256": inputs.model_sha256,
        "runtime_receipt_sha256": inputs.runtime_receipt_sha256,
        "verifier_id": verifier_lock["verifier_id"],
        "verifier_lock_sha256": inputs.expected_verifier_lock_sha256,
        "actor_receipt_sha256": sha256_file(inputs.actor_receipt_json),
        "request_sha256": request_hash,
        "statement_sha256": str(source["sha256"]),
        "selected_path_sha256": path_hash,
        "initial_state_sha256": initial_state_hash,
        "final_state_sha256": final_state_hash,
        "result_sha256": sha256_file(inputs.result_json),
        "raw_tree_sha256": sha256_file(inputs.raw_tree_json),
        "service_receipts_sha256": sha256_file(inputs.service_receipts_jsonl),
        "proof_sha256": hashlib.sha256(proof_script.encode("utf-8")).hexdigest(),
        "theorem_sha256": sha256_file(theorem),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "forbidden_tokens": forbidden,
        "diagnostics": diagnostics,
        "lean_version": version,
        "lean_githash": githash,
        "lean_toolchain": toolchain,
        "mathlib_revision": mathlib_revision,
        "lake_manifest_sha256": manifest_hash,
        "lake_executable": str(resolved_lake),
        "lake_executable_sha256": inputs.expected_lake_sha256,
        "command": command,
        "timeout_seconds": inputs.timeout_seconds,
    }
    kernel_receipt["kernel_receipt_sha256"] = object_hash(kernel_receipt)
    kernel_path = inputs.output_dir / "kernel_receipt.json"
    kernel_path.write_text(json.dumps(kernel_receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt_path = inputs.output_dir / "receipt.json"
    if strict_solved:
        signed_receipt = sign_actor_receipt(
            actor_receipt,
            verifier_lock=verifier_lock,
            verifier_lock_sha256=inputs.expected_verifier_lock_sha256,
            request_sha256=request_hash,
            statement_sha256=str(source["sha256"]),
            selected_path_sha256=path_hash,
            initial_state_sha256=initial_state_hash,
            final_state_sha256=final_state_hash,
            kernel_receipt_sha256=sha256_file(kernel_path),
            private_key_path=key_path,
        )
        receipt_path.write_text(json.dumps(signed_receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        receipt_path.write_text(json.dumps(kernel_receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return (0 if strict_solved else 1), receipt_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--expected-problems-sha256", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--raw-tree-json", type=Path, required=True)
    parser.add_argument("--service-receipts-jsonl", type=Path, required=True)
    parser.add_argument("--actor-receipt-json", type=Path, required=True)
    parser.add_argument("--generated-sha256", required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--runtime-receipt-sha256", required=True)
    parser.add_argument("--expected-course-manifest-sha256", required=True)
    parser.add_argument("--expected-lake-sha256", required=True)
    parser.add_argument("--verifier-lock", type=Path, required=True)
    parser.add_argument("--expected-verifier-lock-sha256", required=True)
    parser.add_argument("--verifier-private-key", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--course-project", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lake", default="lake")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    code, receipt = run_strict_replay(ReplayInputs(
        problems=args.problems, expected_problems_sha256=args.expected_problems_sha256,
        source_id=args.source_id, result_json=args.result_json, raw_tree_json=args.raw_tree_json,
        service_receipts_jsonl=args.service_receipts_jsonl,
        actor_receipt_json=args.actor_receipt_json, generated_sha256=args.generated_sha256,
        model_sha256=args.model_sha256, runtime_receipt_sha256=args.runtime_receipt_sha256,
        expected_course_manifest_sha256=args.expected_course_manifest_sha256,
        expected_lake_sha256=args.expected_lake_sha256,
        verifier_lock=args.verifier_lock,
        expected_verifier_lock_sha256=args.expected_verifier_lock_sha256,
        verifier_private_key=args.verifier_private_key, workspace_root=args.workspace_root,
        course_project=args.course_project, output_dir=args.output_dir, lake=args.lake,
        timeout_seconds=args.timeout_seconds,
    ))
    print(receipt.read_text(encoding="utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

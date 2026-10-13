#!/usr/bin/env python3
"""Adapt one real CE/Online command to the reviewed orchestrator contract.

The worker owns only the small control receipts.  Model checkpoints stay in the
arm's native output directory and are never copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


class WorkerError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def append_event(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_dir(path.parent)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def verify_pin(root: Path, pin: Mapping[str, Any], label: str) -> Path:
    path = resolve(root, str(pin.get("path", "")))
    expected = str(pin.get("sha256", ""))
    if len(expected) != 64 or not path.is_file():
        raise WorkerError(f"{label} pin is unresolved: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise WorkerError(f"{label} hash mismatch: expected {expected}, got {actual}")
    return path


def pointer_value(path: Path, dotted_key: str) -> Any:
    value = load_json(path)
    for component in dotted_key.split("."):
        if not isinstance(value, dict) or component not in value:
            raise WorkerError(f"resume pointer lacks {dotted_key}: {path}")
        value = value[component]
    return value


def render_command(parts: Sequence[str], variables: Mapping[str, str]) -> list[str]:
    rendered: list[str] = []
    for part in parts:
        try:
            rendered.append(part.format_map(variables))
        except KeyError as exc:
            raise WorkerError(f"unknown command placeholder: {exc.args[0]}") from exc
    return rendered


def checkpoint_commit(control: Path, event_log: Path, stage_id: str, unit: int,
                      payload: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = control / "checkpoint.json"
    receipt = control / "checkpoint_receipt.json"
    atomic_json(checkpoint, {
        "schema_version": 1, "stage": stage_id, "last_committed_unit": unit,
        "payload": dict(payload), "committed_at": time.time(),
    })
    committed = event_log.stat().st_size if event_log.exists() else 0
    if not event_log.exists():
        event_log.parent.mkdir(parents=True, exist_ok=True)
        event_log.touch()
        with event_log.open("rb") as handle:
            os.fsync(handle.fileno())
    event_digest = hashlib.sha256(event_log.read_bytes()).hexdigest()
    result = {
        "schema_version": 1,
        "status": "COMMITTED",
        "stage": stage_id,
        "input_fingerprint": os.environ["EXPERIMENT_INPUT_FINGERPRINT"],
        "stage_fingerprint": os.environ["EXPERIMENT_STAGE_FINGERPRINT"],
        "last_committed_unit": unit,
        "checkpoint_sha256": sha256_file(checkpoint),
        "event_log_committed_bytes": committed,
        "event_log_prefix_sha256": event_digest,
    }
    atomic_json(receipt, result)
    return result


def _native_checkpoint_evidence(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"path": str(path), "kind": "file", "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size}
    if path.is_dir():
        records = []
        for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
            records.append({"path": item.relative_to(path).as_posix(),
                            "sha256": sha256_file(item), "size_bytes": item.stat().st_size})
        return {"path": str(path), "kind": "directory",
                "manifest_sha256": canonical_sha256(records), "files": records}
    raise WorkerError(f"native checkpoint is missing: {path}")


def execute(protocol_path: Path, stage_id: str, resume_checkpoint: Path | None) -> int:
    protocol_path = protocol_path.resolve()
    protocol = load_json(protocol_path)
    root = resolve(protocol_path.parent, str(protocol["experiment_root"]))
    stage = next((item for item in protocol["stages"] if item["id"] == stage_id), None)
    if stage is None:
        raise WorkerError(f"stage not found in protocol: {stage_id}")
    if os.environ.get("EXPERIMENT_STAGE_ID") != stage_id:
        raise WorkerError("worker must run under experiment_orchestrator")
    for label, pin in protocol["shared_inputs"].items():
        verify_pin(root, pin, f"shared input {label}")
    verify_pin(root, stage["arm_config"], "arm config")

    control = resolve(root, stage["control_dir"])
    native_output = resolve(root, stage["native_output_dir"])
    event_log = control / "events.jsonl"
    progress = resolve(root, stage["progress_file"])
    control.mkdir(parents=True, exist_ok=True)
    native_output.parent.mkdir(parents=True, exist_ok=True)

    variables = {
        "experiment_root": str(root), "protocol": str(protocol_path),
        "stage_id": stage_id, "native_output": str(native_output),
        "arm_config": str(resolve(root, stage["arm_config"]["path"])),
        "arm_config_sha256": str(stage["arm_config"]["sha256"]),
        "wave_index": str(stage["wave_index"]),
    }
    for name, pin in protocol["shared_inputs"].items():
        variables[f"shared_{name}_path"] = str(resolve(root, pin["path"]))
        variables[f"shared_{name}_sha256"] = str(pin["sha256"])
    command = render_command(stage["command"], variables)

    if resume_checkpoint is None:
        if (control / "checkpoint_receipt.json").exists() or native_output.exists():
            raise WorkerError("existing worker/native output requires orchestrator --resume")
        append_event(event_log, {"event": "stage_started", "stage": stage_id,
                                 "wave_index": stage["wave_index"], "time": time.time()})
        checkpoint_commit(control, event_log, stage_id, 0,
                          {"state": "READY", "native_output": str(native_output)})
    else:
        expected = (control / "checkpoint.json").resolve()
        if resume_checkpoint.resolve() != expected or not expected.is_file():
            raise WorkerError("orchestrator supplied a foreign resume checkpoint")
        resume_variables = dict(variables)
        pointer = stage.get("native_resume_pointer")
        if pointer is not None:
            pointer_path = resolve(root, pointer["path"])
            resume_variables["native_checkpoint"] = str(pointer_value(pointer_path, pointer["json_key"]))
        command.extend(render_command(stage["resume_command_append"], resume_variables))
        # Do not rewrite unit 0: the supervisor seals its exact bytes after a
        # failed attempt and correctly rejects a same-unit evidence fork.

    atomic_json(progress, {"completed": 0, "total": 1, "unit": "update",
                           "aggregate_rate": 0.0, "stage": stage_id})
    print(json.dumps({"event": "paired-worker-start", "stage": stage_id,
                      "arm": stage["arm"], "wave_index": stage["wave_index"],
                      "command": command}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=resolve(root, stage["cwd"]), env=os.environ.copy())
    while process.poll() is None:
        elapsed = time.monotonic() - started
        if int(elapsed) and int(elapsed) % 20 == 0:
            atomic_json(progress, {"completed": 0, "total": 1, "unit": "update",
                                   "aggregate_rate": 0.0, "stage": stage_id,
                                   "elapsed_seconds": round(elapsed, 3)})
            print(json.dumps({"event": "paired-worker-heartbeat", "stage": stage_id,
                              "elapsed_seconds": round(elapsed, 3)}), flush=True)
            time.sleep(1.0)
        else:
            time.sleep(0.25)
    if process.returncode != 0:
        atomic_json(control / "last_native_failure.json", {
            "stage": stage_id, "exit_code": process.returncode, "time": time.time(),
            "note": "diagnostic only; committed unit-0 evidence is intentionally unchanged",
        })
        return int(process.returncode or 1)

    terminal = resolve(root, stage["native_terminal"])
    if not terminal.is_file():
        raise WorkerError(f"native terminal receipt missing: {terminal}")
    terminal_payload = load_json(terminal)
    status = terminal_payload
    for component in stage["terminal_status_json_key"].split("."):
        if not isinstance(status, dict) or component not in status:
            raise WorkerError(f"native terminal lacks status key: {terminal}")
        status = status[component]
    if status not in stage["terminal_success_values"]:
        raise WorkerError(f"native terminal is not successful: {status!r}")
    native_checkpoint = resolve(root, stage["native_checkpoint"])
    native_evidence = _native_checkpoint_evidence(native_checkpoint)
    pointer = stage.get("native_resume_pointer")
    if pointer is not None:
        pointed = Path(str(pointer_value(resolve(root, pointer["path"]), pointer["json_key"])))
        if not pointed.is_absolute():
            pointed = resolve(root, str(pointed))
        native_evidence = {
            "pointer": native_evidence,
            "pointed_checkpoint": _native_checkpoint_evidence(pointed.resolve()),
        }
    result = {
        "schema_version": 1, "status": "DONE", "stage": stage_id,
        "arm": stage["arm"], "mode": stage["mode"], "wave_index": stage["wave_index"],
        "seed": protocol["seed"], "protocol_sha256": sha256_file(protocol_path),
        "terminal": {"path": str(terminal), "sha256": sha256_file(terminal),
                     "size_bytes": terminal.stat().st_size},
        "native_checkpoint": native_evidence,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    result_path = control / "result.json"
    atomic_json(result_path, result)
    append_event(event_log, {"event": "stage_committed", "stage": stage_id,
                             "result_sha256": sha256_file(result_path), "time": time.time()})
    checkpoint_commit(control, event_log, stage_id, 1,
                      {"state": "DONE", "result_sha256": sha256_file(result_path),
                       "native_checkpoint": native_evidence})
    success = {
        "schema_version": 1, "status": "DONE", "stage": stage_id,
        "input_fingerprint": os.environ["EXPERIMENT_INPUT_FINGERPRINT"],
        "stage_fingerprint": os.environ["EXPERIMENT_STAGE_FINGERPRINT"],
        "outputs": {"result": {"sha256": sha256_file(result_path),
                                "size_bytes": result_path.stat().st_size}},
    }
    atomic_json(control / "success_receipt.json", success)
    atomic_json(progress, {"completed": 1, "total": 1, "unit": "update",
                           "aggregate_rate": round(1 / max(time.monotonic() - started, 1e-9), 8),
                           "stage": stage_id})
    print(json.dumps({"event": "paired-worker-done", "stage": stage_id,
                      "result": str(result_path)}, ensure_ascii=False), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--resume-checkpoint", type=Path)
    args = parser.parse_args(argv)
    try:
        return execute(args.protocol, args.stage_id, args.resume_checkpoint)
    except (WorkerError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"event": "paired-worker-failed", "stage": args.stage_id,
                          "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

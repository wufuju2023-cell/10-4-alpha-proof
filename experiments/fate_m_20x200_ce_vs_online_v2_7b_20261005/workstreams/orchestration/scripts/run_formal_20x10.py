#!/usr/bin/env python3
"""Run the frozen 20x10 CE/Online-v2 experiment as a resumable evidence DAG.

This is deliberately only an orchestrator.  Every GPU/Lean unit is delegated
to a configured production command and is accepted only after that command
publishes a hash-bound ``UNIT_DONE.json``.  The driver never manufactures a
rollout, verifier result, learner checkpoint, or evaluation result.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


HERE = Path(__file__).resolve()
ORCHESTRATION_ROOT = HERE.parents[1]
sys.path.insert(0, str(ORCHESTRATION_ROOT / "src"))

from experiment_orchestrator import (  # noqa: E402
    StorageGuardError,
    atomic_json,
    gpu_metrics,
    read_storage_observation,
    sha256_file,
    storage_admission,
)


CONFIG_SCHEMA = "fate.formal_20x10.orchestrator.v1"
RECEIPT_SCHEMA = "fate.formal_20x10.unit_receipt.v1"
STATE_SCHEMA = "fate.formal_20x10.state.v1"
HEX = frozenset("0123456789abcdef")
ARMS = ("ce", "online_v2")
WAVES = tuple(range(1, 9))


class FormalRunError(RuntimeError):
    pass


class DeadlineReached(FormalRunError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FormalRunError(message)


def _sha(value: object, label: str) -> str:
    _require(isinstance(value, str) and len(value) == 64 and
             all(ch in HEX for ch in value.lower()), f"{label} is not SHA-256")
    return str(value).lower()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormalRunError(f"unreadable JSON: {path}") from exc
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _pin(root: Path, value: object, label: str) -> Path:
    _require(isinstance(value, Mapping) and set(value) == {"path", "sha256"},
             f"{label} must contain exactly path/sha256")
    path = _resolve(root, str(value["path"]))
    expected = _sha(value["sha256"], f"{label}.sha256")
    _require(path.is_file(), f"{label} is missing: {path}")
    _require(sha256_file(path) == expected, f"{label} hash mismatch: {path}")
    return path


def _has_placeholder(value: object) -> bool:
    if isinstance(value, str):
        return "<" in value or "FILL_" in value or "NEW-JOIN" in value
    if isinstance(value, list):
        return any(_has_placeholder(item) for item in value)
    if isinstance(value, Mapping):
        return any(_has_placeholder(item) for item in value.values())
    return False


def validate_config(config_path: Path, protocol_path: Path,
                    hard_deadline_seconds: int) -> tuple[dict[str, Any], Path]:
    config = _load_json(config_path)
    _require(config.get("schema_version") == CONFIG_SCHEMA, "wrong formal config schema")
    _require(config.get("status") == "frozen", "formal config must have status=frozen")
    _require(not _has_placeholder(config), "formal config still contains placeholders")
    _require(hard_deadline_seconds == 21600, "frozen protocol requires exactly 21600 seconds")
    root = _resolve(config_path.parent, str(config.get("experiment_root", "")))
    _require(root.is_dir(), f"experiment_root is missing: {root}")
    protocol = _load_json(protocol_path)
    _require(protocol.get("status") == "frozen_before_execution", "protocol is not frozen")
    _require(protocol.get("selection", {}).get("train_variant_indices") == list(WAVES),
             "protocol does not define exactly eight train waves")
    _require(protocol.get("selection", {}).get("heldout_variant_indices") == [9, 10],
             "protocol heldout must be v009-v010")
    budget = protocol.get("budget", {})
    _require(budget.get("max_attempts_per_task") == 4 and
             budget.get("max_new_tokens_per_attempt") == 512,
             "protocol must freeze four attempts and 512 tokens")
    protocol_pin = config.get("protocol")
    pinned_protocol = _pin(root, protocol_pin, "protocol")
    _require(pinned_protocol == protocol_path.resolve(), "--protocol differs from config pin")
    datasets = config.get("datasets")
    _require(isinstance(datasets, Mapping), "datasets pins are missing")
    _pin(root, datasets.get("train"), "datasets.train")
    _pin(root, datasets.get("heldout"), "datasets.heldout")
    _pin(root, config.get("initial_checkpoint"), "initial_checkpoint")
    guard = config.get("storage_guard")
    _require(isinstance(guard, Mapping), "storage_guard is missing")
    _require(guard.get("expected_capacity_gib") == 100 and
             guard.get("warn_used_gib") == 90 and guard.get("stop_used_gib") == 95,
             "storage guard must be 100/90/95 GiB")
    _require(isinstance(guard.get("max_age_seconds"), int) and
             15 <= guard["max_age_seconds"] <= 60,
             "storage observation max age must be 15..60 seconds")
    recipes = config.get("recipes")
    _require(isinstance(recipes, Mapping) and set(recipes) == {"evaluation", "rollout", "join", "update"},
             "recipes must be exactly evaluation/rollout/join/update")
    for name, recipe in recipes.items():
        _require(isinstance(recipe, Mapping), f"recipe {name} is not an object")
        _require(set(recipe) == {"argv", "max_additional_gib"},
                 f"recipe {name} fields must be argv/max_additional_gib")
        argv = recipe["argv"]
        _require(isinstance(argv, list) and argv and all(isinstance(x, str) and x for x in argv),
                 f"recipe {name}.argv is invalid")
        growth = recipe["max_additional_gib"]
        _require(isinstance(growth, (int, float)) and not isinstance(growth, bool)
                 and 0 < float(growth) < 5, f"recipe {name}.max_additional_gib is invalid")
    return config, root


def build_plan() -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = [
        {"id": "baseline_eval", "kind": "evaluation", "arm": "shared", "wave": 0,
         "split": "heldout", "checkpoint_role": "initial"}
    ]
    for wave in WAVES:
        for arm in ARMS:
            plan.extend((
                {"id": f"w{wave:03d}_{arm}_rollout", "kind": "rollout", "arm": arm,
                 "wave": wave, "split": "train"},
                {"id": f"w{wave:03d}_{arm}_join", "kind": "join", "arm": arm,
                 "wave": wave, "split": "train"},
                {"id": f"w{wave:03d}_{arm}_update", "kind": "update", "arm": arm,
                 "wave": wave, "split": "train"},
            ))
        plan.append({"id": f"w{wave:03d}_common_commit", "kind": "common_commit",
                     "arm": "paired", "wave": wave, "split": "train"})
    plan.append({"id": "lock_final_checkpoints", "kind": "lock_finals", "arm": "paired",
                 "wave": 8, "split": "none"})
    for arm in ARMS:
        plan.append({"id": f"{arm}_final_eval", "kind": "evaluation", "arm": arm,
                     "wave": 8, "split": "heldout", "checkpoint_role": "final"})
    return plan


def _context(root: Path, run_root: Path, protocol: Path, config: Mapping[str, Any],
             unit: Mapping[str, Any], completed: Mapping[str, Any]) -> dict[str, str]:
    arm, wave = str(unit["arm"]), int(unit["wave"])
    unit_dir = run_root / "units" / str(unit["id"])
    arm_dir = run_root / "arms" / arm if arm in ARMS else run_root / "shared"
    checkpoint_sha: str
    if arm == "shared" or wave == 1:
        checkpoint = _resolve(root, config["initial_checkpoint"]["path"])
        checkpoint_sha = str(config["initial_checkpoint"]["sha256"])
    else:
        parent_wave = 8 if unit["kind"] == "evaluation" else wave - 1
        parent = f"w{parent_wave:03d}_{arm}_update"
        _require(parent in completed, f"{unit['id']} lacks parent checkpoint {parent}")
        checkpoint_record = completed[parent]["artifacts"]["checkpoint"]
        checkpoint = Path(checkpoint_record["path"])
        checkpoint_sha = str(checkpoint_record["sha256"])
    return {
        "python": sys.executable,
        "experiment_root": str(root), "run_root": str(run_root),
        "protocol": str(protocol), "unit_dir": str(unit_dir), "unit_id": str(unit["id"]),
        "arm": arm, "wave": str(wave), "split": str(unit["split"]),
        "input_checkpoint": str(checkpoint), "input_checkpoint_sha256": checkpoint_sha,
        "train_records": str(_resolve(root, config["datasets"]["train"]["path"])),
        "heldout_records": str(_resolve(root, config["datasets"]["heldout"]["path"])),
        "rollout_dir": str(run_root / "units" / f"w{wave:03d}_{arm}_rollout"),
        "join_dir": str(run_root / "units" / f"w{wave:03d}_{arm}_join"),
        "receipt": str(unit_dir / "UNIT_DONE.json"),
    }


def _render(argv: Sequence[str], context: Mapping[str, str]) -> list[str]:
    try:
        rendered = [part.format_map(context) for part in argv]
    except KeyError as exc:
        raise FormalRunError(f"unknown command placeholder: {exc}") from exc
    _require(not _has_placeholder(rendered), "rendered command contains an unresolved placeholder")
    return rendered


def _artifact_map(receipt: Mapping[str, Any], unit_dir: Path) -> dict[str, dict[str, str]]:
    artifacts = receipt.get("artifacts")
    _require(isinstance(artifacts, list) and artifacts, "unit receipt has no artifacts")
    result: dict[str, dict[str, str]] = {}
    for index, item in enumerate(artifacts):
        _require(isinstance(item, Mapping) and set(item) == {"role", "path", "sha256"},
                 f"artifact[{index}] fields are invalid")
        role = str(item["role"])
        _require(role and role not in result, f"duplicate artifact role: {role}")
        path = _resolve(unit_dir, str(item["path"]))
        expected = _sha(item["sha256"], f"artifact[{index}].sha256")
        _require(path.is_file() and sha256_file(path) == expected,
                 f"artifact differs from receipt: {path}")
        result[role] = {"path": str(path), "sha256": expected}
    return result


def validate_unit_receipt(path: Path, unit: Mapping[str, Any], protocol_sha: str,
                          config_sha: str) -> dict[str, Any]:
    receipt = _load_json(path)
    _require(receipt.get("schema_version") == RECEIPT_SCHEMA, "wrong unit receipt schema")
    _require(receipt.get("status") == "complete", "unit receipt is not complete")
    _require(receipt.get("unit_id") == unit["id"], "unit receipt unit_id mismatch")
    for key in ("kind", "arm", "wave"):
        _require(receipt.get(key) == unit[key], f"unit receipt {key} mismatch")
    _require(receipt.get("protocol_sha256") == protocol_sha, "unit protocol hash mismatch")
    _require(receipt.get("config_sha256") == config_sha, "unit config hash mismatch")
    artifacts = _artifact_map(receipt, path.parent)
    kind = unit["kind"]
    metrics = receipt.get("metrics")
    _require(isinstance(metrics, Mapping), "unit receipt metrics are missing")
    if kind == "evaluation":
        _require(receipt.get("split") == "heldout" and metrics.get("problems") == 40,
                 "evaluation must cover all 40 heldout problems")
        _require(metrics.get("gradients_enabled") is False, "evaluation enabled gradients")
        _require("evaluation_results" in artifacts, "evaluation results artifact is missing")
    elif kind == "rollout":
        _require(receipt.get("split") == "train" and metrics.get("problems") == 20,
                 "rollout must cover exactly one 20-family train wave")
        _require(metrics.get("max_attempts_per_problem") == 4 and
                 metrics.get("max_new_tokens_per_attempt") == 512,
                 "rollout did not enforce four attempts / 512 tokens")
        _require("rollout_manifest" in artifacts, "rollout manifest is missing")
    elif kind == "join":
        _require({"signed_receipts", "ce_receipts", "online_receipts"}.issubset(artifacts),
                 "join lacks same-source signed/CE/Online receipts")
        _require(isinstance(receipt.get("source_rollout_receipt_sha256"), str),
                 "join does not bind its rollout receipt")
    elif kind == "update":
        _require({"checkpoint", "update_receipt"}.issubset(artifacts),
                 "update lacks persistent checkpoint/update receipt")
        _require(isinstance(metrics.get("optimizer_steps"), int) and
                 metrics["optimizer_steps"] > 0, "update has no optimizer step")
        _require(isinstance(receipt.get("source_join_receipt_sha256"), str),
                 "update does not bind its join receipt")
    return {"path": str(path), "sha256": sha256_file(path), "receipt": receipt,
            "artifacts": artifacts}


def _terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _storage(config: Mapping[str, Any], root: Path, recipe: Mapping[str, Any],
             envelope: Mapping[str, float] | None = None) -> tuple[dict[str, Any], dict[str, float]]:
    guard = config["storage_guard"]
    observation = read_storage_observation(_resolve(root, guard["observation_file"]), guard)
    stage = {"expensive": True, "max_additional_gib": float(recipe["max_additional_gib"])}
    checked = storage_admission(observation, guard, stage, envelope)
    return observation, checked


def _run_external(config: Mapping[str, Any], root: Path, run_root: Path,
                  protocol: Path, unit: Mapping[str, Any], deadline_at: float,
                  protocol_sha: str, config_sha: str,
                  completed: Mapping[str, Any]) -> dict[str, Any]:
    recipe = config["recipes"][unit["kind"]]
    context = _context(root, run_root, protocol, config, unit, completed)
    unit_dir = Path(context["unit_dir"])
    resuming_unit = unit_dir.exists()
    unit_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = unit_dir / "UNIT_DONE.json"
    _require(not receipt_path.exists(), f"unsealed existing unit output: {unit_dir}")
    storage, envelope = _storage(config, root, recipe)
    argv = _render(recipe["argv"], context)
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in config.get("environment", {}).items()})
    env["FATE_FORMAL_RESUME"] = "1" if resuming_unit else "0"
    print(json.dumps({"event": "unit_start", "unit": unit["id"], "argv": argv,
                      "storage": storage, "gpu": gpu_metrics()}, sort_keys=True), flush=True)
    process = subprocess.Popen(argv, cwd=root, env=env,
                               start_new_session=(os.name == "posix"))
    next_check = 0.0
    while process.poll() is None:
        now = time.time()
        if now >= deadline_at:
            _terminate(process)
            raise DeadlineReached(f"hard deadline reached during {unit['id']}")
        if now >= next_check:
            try:
                storage, _ = _storage(config, root, recipe, envelope)
            except StorageGuardError:
                _terminate(process)
                raise
            print(json.dumps({"event": "heartbeat", "unit": unit["id"],
                              "remaining_seconds": round(deadline_at - now, 1),
                              "storage": storage, "gpu": gpu_metrics()}, sort_keys=True), flush=True)
            next_check = now + 20
        time.sleep(1)
    _require(process.returncode == 0, f"unit command failed ({process.returncode}): {unit['id']}")
    _require(receipt_path.is_file(), f"unit did not publish UNIT_DONE.json: {unit['id']}")
    result = validate_unit_receipt(receipt_path, unit, protocol_sha, config_sha)
    receipt = result["receipt"]
    if unit["kind"] in {"evaluation", "rollout"}:
        _require(receipt.get("input_checkpoint_sha256") == context["input_checkpoint_sha256"],
                 f"{unit['id']} used a different checkpoint")
    if unit["kind"] == "join":
        parent = f"w{int(unit['wave']):03d}_{unit['arm']}_rollout"
        _require(parent in completed, "join lacks its completed rollout")
        _require(receipt.get("source_rollout_receipt_sha256") == completed[parent]["sha256"],
                 "join is not derived from its arm's rollout receipt")
        canonical = receipt.get("canonical_source_receipt_sha256")
        _require(isinstance(canonical, str) and
                 receipt.get("ce_source_receipt_sha256") == canonical and
                 receipt.get("online_source_receipt_sha256") == canonical,
                 "join CE/Online outputs are not derived from one signed source")
    elif unit["kind"] == "update":
        parent = f"w{int(unit['wave']):03d}_{unit['arm']}_join"
        _require(parent in completed, "update lacks its completed join")
        _require(receipt.get("source_join_receipt_sha256") == completed[parent]["sha256"],
                 "update is not derived from its arm's joined receipt")
    return result


def _atomic_control_receipt(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    atomic_json(path, value)
    return {"path": str(path), "sha256": sha256_file(path), "receipt": dict(value),
            "artifacts": {}}


def _control_unit(run_root: Path, unit: Mapping[str, Any], completed: Mapping[str, Any],
                  protocol_sha: str, config_sha: str) -> dict[str, Any]:
    unit_dir = run_root / "units" / str(unit["id"])
    unit_dir.mkdir(parents=True, exist_ok=False)
    if unit["kind"] == "common_commit":
        wave = int(unit["wave"])
        parents = [f"w{wave:03d}_{arm}_update" for arm in ARMS]
        _require(all(parent in completed for parent in parents), "common commit lacks both updates")
        payload = {"schema_version": RECEIPT_SCHEMA, "status": "complete",
                   "unit_id": unit["id"], "kind": unit["kind"], "arm": unit["arm"],
                   "wave": unit["wave"], "split": unit["split"],
                   "protocol_sha256": protocol_sha, "config_sha256": config_sha,
                   "parents": {parent: completed[parent]["sha256"] for parent in parents},
                   "metrics": {}, "artifacts": []}
        return _atomic_control_receipt(unit_dir / "UNIT_DONE.json", payload)
    _require(unit["kind"] == "lock_finals", "unknown internal control unit")
    parents = [f"w008_{arm}_update" for arm in ARMS]
    _require(all(parent in completed for parent in parents), "final lock lacks wave-8 updates")
    checkpoints = {}
    for parent in parents:
        info = completed[parent]
        checkpoints[parent] = info["artifacts"]["checkpoint"]
    payload = {"schema_version": RECEIPT_SCHEMA, "status": "complete",
               "unit_id": unit["id"], "kind": unit["kind"], "arm": unit["arm"],
               "wave": unit["wave"], "split": unit["split"],
               "protocol_sha256": protocol_sha, "config_sha256": config_sha,
               "parents": {parent: completed[parent]["sha256"] for parent in parents},
               "checkpoints": checkpoints, "metrics": {}, "artifacts": []}
    return _atomic_control_receipt(unit_dir / "UNIT_DONE.json", payload)


def _write_terminal(run_root: Path, state: dict[str, Any], status: str, reason: str | None) -> None:
    state["status"] = status
    state["reason"] = reason
    state["updated_at"] = time.time()
    atomic_json(run_root / "STATE.json", state)
    atomic_json(run_root / status, {"schema_version": STATE_SCHEMA, "status": status,
                                   "reason": reason, "completed_units": len(state["completed"]),
                                   "state_sha256": sha256_file(run_root / "STATE.json")})


def run(config_path: Path, protocol_path: Path, run_root_arg: Path,
        deadline_seconds: int, resume: bool) -> int:
    config, root = validate_config(config_path, protocol_path, deadline_seconds)
    run_root = run_root_arg.resolve() if run_root_arg.is_absolute() else (root / run_root_arg).resolve()
    _require(run_root == root or root in run_root.parents, "run-root must stay inside experiment_root")
    config_sha, protocol_sha = sha256_file(config_path), sha256_file(protocol_path)
    state_path = run_root / "STATE.json"
    if resume:
        state = _load_json(state_path)
        _require(state.get("schema_version") == STATE_SCHEMA, "wrong resume state schema")
        _require(state.get("config_sha256") == config_sha and
                 state.get("protocol_sha256") == protocol_sha, "resume inputs changed")
        _require(state.get("status") in {"RUNNING", "INCOMPLETE", "FAILED"},
                 "resume state is not resumable")
    else:
        _require(not run_root.exists(), "run-root exists; use --resume")
        run_root.mkdir(parents=True)
        started = time.time()
        state = {"schema_version": STATE_SCHEMA, "status": "RUNNING",
                 "config_sha256": config_sha, "protocol_sha256": protocol_sha,
                 "started_at": started, "deadline_at": started + deadline_seconds,
                 "completed": {}, "reason": None, "updated_at": started}
        atomic_json(state_path, state)
    completed = state["completed"]
    _require(isinstance(completed, dict), "completed state is invalid")
    try:
        for unit in build_plan():
            unit_id = unit["id"]
            if unit_id in completed:
                receipt = Path(completed[unit_id]["path"])
                if unit["kind"] not in {"common_commit", "lock_finals"}:
                    verified = validate_unit_receipt(receipt, unit, protocol_sha, config_sha)
                    _require(verified["sha256"] == completed[unit_id]["sha256"],
                             f"resume receipt changed: {unit_id}")
                else:
                    _require(receipt.is_file() and sha256_file(receipt) == completed[unit_id]["sha256"],
                             f"control receipt changed: {unit_id}")
                continue
            if time.time() >= float(state["deadline_at"]):
                raise DeadlineReached(f"hard deadline reached before {unit_id}")
            if unit["kind"] in {"common_commit", "lock_finals"}:
                result = _control_unit(run_root, unit, completed, protocol_sha, config_sha)
            else:
                result = _run_external(config, root, run_root, protocol_path, unit,
                                       float(state["deadline_at"]), protocol_sha, config_sha,
                                       completed)
            completed[unit_id] = {key: result[key] for key in ("path", "sha256", "artifacts")}
            state["status"] = "RUNNING"
            state["updated_at"] = time.time()
            atomic_json(state_path, state)
        _write_terminal(run_root, state, "DONE", None)
        return 0
    except (DeadlineReached, StorageGuardError) as exc:
        _write_terminal(run_root, state, "INCOMPLETE", str(exc))
        print(json.dumps({"event": "formal_incomplete", "reason": str(exc)}), flush=True)
        return 20
    except Exception as exc:
        _write_terminal(run_root, state, "FAILED", f"{type(exc).__name__}: {exc}")
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--hard-deadline-seconds", type=int, default=21600)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--print-plan", action="store_true")
    args = parser.parse_args(argv)
    if args.print_plan:
        print(json.dumps(build_plan(), indent=2), flush=True)
        return 0
    try:
        return run(args.config.resolve(), args.protocol.resolve(), args.run_root,
                   args.hard_deadline_seconds, args.resume)
    except FormalRunError as exc:
        parser.exit(2, f"fail closed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

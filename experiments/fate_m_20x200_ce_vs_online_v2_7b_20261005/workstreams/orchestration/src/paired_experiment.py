#!/usr/bin/env python3
"""Freeze and run the paired CE/Online-v2 orchestration plan."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiment_orchestrator import atomic_json, sha256_file, validate_config


class ProtocolError(ValueError):
    pass


TOP_KEYS = {
    "schema_version", "experiment_id", "experiment_root", "run_root",
    "emergency_dir", "seed", "execution", "storage_guard", "shared_inputs",
    "frozen_environment", "stages",
}
STAGE_KEYS = {
    "id", "arm", "mode", "wave_index", "arm_config", "command", "cwd",
    "native_output_dir", "native_terminal", "native_checkpoint",
    "native_resume_pointer", "resume_command_append", "budget_seconds",
    "max_additional_gib", "control_dir", "progress_file",
    "terminal_status_json_key", "terminal_success_values",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _validate_pin(pin: Any, label: str) -> None:
    _require(isinstance(pin, dict) and set(pin) == {"path", "sha256"},
             f"{label} must contain exactly path/sha256")
    _require(isinstance(pin["path"], str) and bool(pin["path"]), f"{label}.path is empty")
    digest = pin["sha256"]
    _require(isinstance(digest, str) and len(digest) == 64 and
             all(ch in "0123456789abcdef" for ch in digest), f"{label}.sha256 is invalid")


def validate_protocol(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == TOP_KEYS,
             f"protocol keys must be exactly {sorted(TOP_KEYS)}")
    _require(value["schema_version"] == 1, "schema_version must be 1")
    _require(value["execution"] == "sequential_single_gpu",
             "this production plan is sequential_single_gpu")
    _require(isinstance(value["seed"], int) and not isinstance(value["seed"], bool),
             "seed must be an integer")
    for key in ("experiment_id", "experiment_root", "run_root", "emergency_dir"):
        _require(isinstance(value[key], str) and bool(value[key]), f"{key} is empty")
    guard = value["storage_guard"]
    expected_guard = {
        "observation_file", "expected_capacity_gib", "max_age_seconds",
        "warn_used_gib", "stop_used_gib",
    }
    _require(isinstance(guard, dict) and set(guard) == expected_guard,
             "storage_guard fields are incomplete")
    _require(guard["expected_capacity_gib"] == 100 and guard["warn_used_gib"] == 90
             and guard["stop_used_gib"] == 95, "storage limits must be 100/90/95 GiB")
    _require(isinstance(guard["max_age_seconds"], int) and 20 <= guard["max_age_seconds"] <= 60,
             "storage max_age_seconds must be 20..60")
    shared = value["shared_inputs"]
    required_shared = {"course", "initial_adapter", "live_receipts", "actor_config"}
    _require(isinstance(shared, dict) and required_shared.issubset(shared),
             f"shared_inputs must include {sorted(required_shared)}")
    for name, pin in shared.items():
        _validate_pin(pin, f"shared_inputs.{name}")
    environment = value["frozen_environment"]
    _require(isinstance(environment, dict) and set(environment) == {"inherit", "set"},
             "frozen_environment must contain inherit/set")
    _require(isinstance(environment["inherit"], list) and
             all(isinstance(item, str) and item for item in environment["inherit"]),
             "frozen_environment.inherit is invalid")
    _require(isinstance(environment["set"], dict) and
             all(isinstance(k, str) and isinstance(v, str)
                 for k, v in environment["set"].items()),
             "frozen_environment.set is invalid")
    stages = value["stages"]
    _require(isinstance(stages, list) and bool(stages), "stages must be non-empty")
    ids: set[str] = set()
    controls: set[str] = set()
    pairs: dict[tuple[str, int], set[str]] = {}
    for index, stage in enumerate(stages):
        prefix = f"stages[{index}]"
        _require(isinstance(stage, dict) and set(stage) == STAGE_KEYS,
                 f"{prefix} fields differ from the frozen schema")
        _require(stage["id"] not in ids and isinstance(stage["id"], str),
                 f"{prefix}.id must be unique")
        ids.add(stage["id"])
        _require(stage["arm"] in {"ce", "online_v2"}, f"{prefix}.arm is invalid")
        _require(stage["mode"] in {"smoke", "formal"}, f"{prefix}.mode is invalid")
        _require(isinstance(stage["wave_index"], int) and 1 <= stage["wave_index"] <= 175,
                 f"{prefix}.wave_index must be 1..175")
        pair = (stage["mode"], stage["wave_index"])
        _require(stage["arm"] not in pairs.setdefault(pair, set()),
                 f"duplicate {stage['arm']} stage for {pair}")
        pairs[pair].add(stage["arm"])
        _validate_pin(stage["arm_config"], f"{prefix}.arm_config")
        _require(isinstance(stage["command"], list) and bool(stage["command"]) and
                 all(isinstance(item, str) and item for item in stage["command"]),
                 f"{prefix}.command is invalid")
        _require(isinstance(stage["resume_command_append"], list) and
                 all(isinstance(item, str) for item in stage["resume_command_append"]),
                 f"{prefix}.resume_command_append is invalid")
        pointer = stage["native_resume_pointer"]
        _require(pointer is None or (isinstance(pointer, dict) and
                 set(pointer) == {"path", "json_key"} and
                 all(isinstance(pointer[key], str) and pointer[key]
                     for key in ("path", "json_key"))),
                 f"{prefix}.native_resume_pointer is invalid")
        for key in ("cwd", "native_output_dir", "native_terminal", "native_checkpoint",
                    "control_dir", "progress_file", "terminal_status_json_key"):
            _require(isinstance(stage[key], str) and stage[key], f"{prefix}.{key} is empty")
        _require(stage["control_dir"] not in controls, f"duplicate control_dir: {stage['control_dir']}")
        controls.add(stage["control_dir"])
        _require(isinstance(stage["terminal_success_values"], list) and
                 bool(stage["terminal_success_values"]), f"{prefix}.terminal_success_values is empty")
        _require(isinstance(stage["budget_seconds"], int) and stage["budget_seconds"] > 0,
                 f"{prefix}.budget_seconds must be positive")
        growth = stage["max_additional_gib"]
        _require(isinstance(growth, (int, float)) and not isinstance(growth, bool) and 0 < growth < 5,
                 f"{prefix}.max_additional_gib must be in (0,5)")
    incomplete = [pair for pair, arms in pairs.items() if arms != {"ce", "online_v2"}]
    _require(not incomplete, f"each wave/mode must have one CE and one Online-v2 stage: {incomplete}")
    return value


def _validate_fairness_bindings(protocol: Mapping[str, Any], root: Path) -> None:
    """Prove the two wave-1 arm configs consume the declared shared start."""
    adapter_manifest = _resolve(root, protocol["shared_inputs"]["initial_adapter"]["path"])
    adapter_root = adapter_manifest.parent.resolve()
    course = _resolve(root, protocol["shared_inputs"]["course"]["path"])
    actor = _resolve(root, protocol["shared_inputs"]["actor_config"]["path"])
    live_receipt_pin = protocol["shared_inputs"]["live_receipts"]
    live_receipt = _resolve(root, live_receipt_pin["path"])
    _require(live_receipt.is_file(), "shared live receipt does not exist")
    _require(sha256_file(live_receipt) == live_receipt_pin["sha256"],
             "shared live receipt differs from pin")
    signed_payload = json.loads(live_receipt.read_text(encoding="utf-8"))
    signed_payload_sha256 = signed_payload.get("receipt_sha256")
    _require(isinstance(signed_payload_sha256, str) and
             len(signed_payload_sha256) == 64 and
             all(ch in "0123456789abcdef" for ch in signed_payload_sha256),
             "shared live receipt has no valid canonical receipt_sha256")
    checked_wave_one: set[str] = set()
    for stage in protocol["stages"]:
        config_path = _resolve(root, stage["arm_config"]["path"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        train = config.get("train")
        _require(isinstance(train, dict) and train.get("seed") == protocol["seed"],
                 f"{stage['id']} train.seed differs from shared seed")
        if stage["wave_index"] != 1 or stage["arm"] in checked_wave_one:
            continue
        checked_wave_one.add(stage["arm"])
        if stage["arm"] == "ce":
            observed_adapter = config.get("model", {}).get("initial_adapter", {}).get("path")
            locks = config.get("locks", {})
            observed_course = locks.get("course_manifest", {}).get("path")
            observed_actor = locks.get("shared_actor_config", {}).get("path")
            _require(observed_course and _resolve(root, observed_course) == course,
                     "wave-1 CE config does not consume the shared course manifest")
            _require(observed_actor and _resolve(root, observed_actor) == actor,
                     "wave-1 CE config does not consume the shared actor config")
        else:
            observed_adapter = config.get("pins", {}).get("adapter", {}).get("path")
            receipts = config.get("pins", {}).get("receipts")
            _require(isinstance(receipts, list) and bool(receipts),
                     "wave-1 Online-v2 config has no pinned joined receipts")
            for index, receipt in enumerate(receipts):
                label = f"wave-1 Online-v2 receipts[{index}]"
                _require(isinstance(receipt, dict), f"{label} is not an object")
                source_path = receipt.get("source_receipt_path")
                _require(isinstance(source_path, str) and
                         _resolve(root, source_path) == live_receipt,
                         f"{label} does not consume the shared live receipt")
                _require(receipt.get("source_receipt_file_sha256") ==
                         live_receipt_pin["sha256"],
                         f"{label} source receipt file hash differs from shared pin")
                _require(receipt.get("source_receipt_sha256") == signed_payload_sha256,
                         f"{label} canonical source receipt hash differs from shared receipt")
                joined_path_value = receipt.get("path")
                joined_sha256 = receipt.get("sha256")
                _require(isinstance(joined_path_value, str) and
                         isinstance(joined_sha256, str),
                         f"{label} joined receipt pin is incomplete")
                joined_path = _resolve(root, joined_path_value)
                _require(joined_path.is_file() and sha256_file(joined_path) == joined_sha256,
                         f"{label} joined receipt differs from pin")
                joined_payload = json.loads(joined_path.read_text(encoding="utf-8"))
                _require(joined_payload.get("source_receipt_sha256") == signed_payload_sha256,
                         f"{label} joined payload is not derived from the shared receipt")
        _require(observed_adapter and _resolve(root, observed_adapter) == adapter_root,
                 f"wave-1 {stage['arm']} config does not consume the shared initial adapter")
    _require(checked_wave_one == {"ce", "online_v2"},
             "the production protocol must include a paired wave-1 shared initialization")


def freeze_protocol(protocol_path: Path, output_path: Path) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    protocol = validate_protocol(json.loads(protocol_path.read_text(encoding="utf-8")))
    root = _resolve(protocol_path.parent, protocol["experiment_root"])
    _validate_fairness_bindings(protocol, root)
    source = Path(__file__).resolve().parent
    worker = source / "paired_stage_worker.py"
    orchestrator = source / "experiment_orchestrator.py"

    inputs: list[dict[str, Any]] = [
        {"id": "paired_protocol", "path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        {"id": "paired_stage_worker", "path": str(worker), "sha256": sha256_file(worker)},
        {"id": "experiment_orchestrator", "path": str(orchestrator), "sha256": sha256_file(orchestrator)},
    ]
    for name, pin in sorted(protocol["shared_inputs"].items()):
        path = _resolve(root, pin["path"])
        _require(path.is_file() and sha256_file(path) == pin["sha256"],
                 f"shared input differs from pin: {name}")
        inputs.append({"id": f"shared_{name}", "path": str(path), "sha256": pin["sha256"]})
    seen_arm_configs: set[tuple[str, str]] = set()
    for stage in protocol["stages"]:
        pin = stage["arm_config"]
        path = _resolve(root, pin["path"])
        _require(path.is_file() and sha256_file(path) == pin["sha256"],
                 f"arm config differs from pin: {stage['id']}")
        identity = (str(path), pin["sha256"])
        if identity not in seen_arm_configs:
            seen_arm_configs.add(identity)
            inputs.append({"id": f"arm_config_{len(seen_arm_configs):02d}",
                           "path": str(path), "sha256": pin["sha256"]})

    stages = []
    for stage in protocol["stages"]:
        control = stage["control_dir"].rstrip("/")
        stages.append({
            "id": stage["id"],
            "command": ["python3", str(worker), "--protocol", str(protocol_path),
                        "--stage-id", stage["id"]],
            "budget_seconds": stage["budget_seconds"],
            "expensive": True,
            "cwd": stage["cwd"],
            "max_additional_gib": stage["max_additional_gib"],
            "resume_contract": {
                "mode": "checkpoint",
                "checkpoint_path": f"{control}/checkpoint.json",
                "receipt_path": f"{control}/checkpoint_receipt.json",
                "event_log_path": f"{control}/events.jsonl",
                "resume_args": ["--resume-checkpoint", "{checkpoint}"],
                "idempotent": True,
            },
            "success_receipt": f"{control}/success_receipt.json",
            "required_outputs": [{"id": "result", "path": f"{control}/result.json"}],
            "progress_file": stage["progress_file"],
        })
    config = {
        "schema_version": 2,
        "experiment_id": protocol["experiment_id"],
        "experiment_root": str(root),
        "run_root": protocol["run_root"],
        "emergency_dir": protocol["emergency_dir"],
        "control_reserve_bytes": 1048576,
        "heartbeat_seconds": 20,
        "storage_guard": {
            "observation_file": protocol["storage_guard"]["observation_file"],
            "required_source": "modelscope_ui_top_right",
            "expected_capacity_gib": 100,
            "max_age_seconds": protocol["storage_guard"]["max_age_seconds"],
            "warn_used_gib": 90,
            "stop_used_gib": 95,
        },
        "immutable_inputs": inputs,
        "frozen_environment": protocol["frozen_environment"],
        "stages": stages,
    }
    validate_config(config)
    atomic_json(output_path.resolve(), config)
    return {"config": str(output_path.resolve()), "sha256": sha256_file(output_path.resolve()),
            "stages": [stage["id"] for stage in protocol["stages"]],
            "execution": protocol["execution"]}


def run_pair(config_path: Path, protocol_path: Path, mode: str, wave_start: int,
             wave_end: int, resume: bool) -> int:
    config_path = config_path.resolve()
    protocol = validate_protocol(json.loads(protocol_path.read_text(encoding="utf-8")))
    wanted = [stage for stage in protocol["stages"]
              if stage["mode"] == mode and wave_start <= stage["wave_index"] <= wave_end]
    if not wanted:
        raise ProtocolError("no stages match the requested mode/wave range")
    orchestrator = Path(__file__).resolve().parent / "experiment_orchestrator.py"
    for stage in wanted:
        command = [sys.executable, str(orchestrator), "run", "--config", str(config_path),
                   "--stage", stage["id"]]
        if resume:
            command.append("--resume")
        print(json.dumps({"event": "paired-stage-dispatch", "stage": stage["id"],
                          "arm": stage["arm"], "wave_index": stage["wave_index"]}), flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode:
            print(json.dumps({"event": "paired-stage-stop", "stage": stage["id"],
                              "exit_code": result.returncode}), flush=True)
            return result.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--protocol", type=Path, required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--protocol", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run-pair")
    run.add_argument("--protocol", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--mode", choices=("smoke", "formal"), required=True)
    run.add_argument("--wave-start", type=int, required=True)
    run.add_argument("--wave-end", type=int, required=True)
    run.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            value = validate_protocol(json.loads(args.protocol.read_text(encoding="utf-8")))
            result: Any = {"status": "valid", "stages": len(value["stages"])}
        elif args.action == "freeze":
            result = freeze_protocol(args.protocol, args.output)
        else:
            return run_pair(args.config, args.protocol, args.mode,
                            args.wave_start, args.wave_end, args.resume)
    except (ProtocolError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"event": "paired-plan-failed", "error_type": type(exc).__name__,
                          "error": str(exc)}), flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one resumable paired CE/Online-v2 training wave from joined evidence.

This driver deliberately starts *after* the two arm-specific actor/join stages.
It consumes their ``fate.wave.join_inputs.v1`` manifests, accumulates the CE
receipt stream, runs both real 7B learners sequentially, and accepts the wave
only after validating both native terminal receipts and checkpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping, Sequence


HERE = Path(__file__).resolve()
EXPERIMENT_ROOT = HERE.parents[3]
JOIN_SCHEMA = "fate.wave.join_inputs.v1"
DONE_SCHEMA = "fate.paired_train_wave.v1"


class LaunchError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value: object, excluded_key: str | None = None) -> str:
    if excluded_key is not None:
        if not isinstance(value, dict):
            raise TypeError("excluded_key requires an object")
        value = {key: item for key, item in value.items() if key != excluded_key}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchError(f"unreadable JSON: {source}") from exc
    if not isinstance(value, dict):
        raise LaunchError(f"JSON root is not an object: {source}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def require_file_pin(value: Mapping[str, Any], label: str) -> Path:
    path = Path(str(value.get("path", ""))).resolve()
    expected = str(value.get("sha256", ""))
    if len(expected) != 64 or not path.is_file() or sha256_file(path) != expected:
        raise LaunchError(f"{label} is missing or differs from its SHA-256 pin: {path}")
    return path


def validate_join_manifest(path: Path, wave_index: int) -> dict[str, Any]:
    manifest = load_json(path)
    if manifest.get("schema_version") != JOIN_SCHEMA or manifest.get("status") != "complete":
        raise LaunchError(f"join manifest is not terminal complete: {path}")
    if manifest.get("wave_index") != wave_index:
        raise LaunchError(f"join manifest wave mismatch: {path}")
    sessions = manifest.get("sessions")
    suffix = f"_v{wave_index:03d}"
    if (not isinstance(sessions, list) or len(sessions) != 20
            or len(set(sessions)) != 20
            or any(not isinstance(item, str) or not item.endswith(suffix) for item in sessions)):
        raise LaunchError(f"join manifest must bind 20 unique wave-{wave_index} sessions")
    require_file_pin(manifest.get("ce_receipts", {}), "CE receipts")
    pins_path = require_file_pin(
        manifest.get("online_receipt_pins_file", {}), "Online receipt pin file"
    )
    pins = manifest.get("online_receipt_pins")
    if not isinstance(pins, list) or len(pins) != 20:
        raise LaunchError("join manifest must contain exactly 20 Online receipt pins")
    if json.loads(pins_path.read_text(encoding="utf-8")) != pins:
        raise LaunchError("Online receipt pins disagree with their pinned file")
    for index, pin in enumerate(pins, 1):
        if not isinstance(pin, Mapping):
            raise LaunchError(f"Online receipt pin {index} is not an object")
        require_file_pin(pin, f"Online joined receipt {index}")
        require_file_pin({
            "path": pin.get("source_receipt_path"),
            "sha256": pin.get("source_receipt_file_sha256"),
        }, f"Online signed source receipt {index}")
    return manifest


def joined_behavior_adapter_name(manifest: Mapping[str, Any]) -> str:
    """Recover the PEFT alias bound into the joined behavior-state hash.

    The actor protocol freezes ``behavior_adapter_alias == behavior_version``.
    Since the state digest includes full parameter names, all joined search
    receipts must agree on that version and Online-v2 must reload the adapter
    under exactly that name.
    """

    pins = manifest.get("online_receipt_pins")
    if not isinstance(pins, list) or not pins:
        raise LaunchError("join manifest lacks Online receipt pins")
    versions: set[str] = set()
    for index, pin in enumerate(pins, 1):
        if not isinstance(pin, Mapping):
            raise LaunchError(f"Online receipt pin {index} is not an object")
        joined = load_json(require_file_pin(pin, f"Online joined receipt {index}"))
        searches = joined.get("searches")
        if not isinstance(searches, list) or not searches:
            raise LaunchError(f"Online joined receipt {index} has no search receipts")
        for search in searches:
            if not isinstance(search, Mapping):
                raise LaunchError(f"Online joined receipt {index} has a malformed search receipt")
            behavior_version = search.get("behavior_version")
            policy_version = search.get("policy_version")
            if (not isinstance(behavior_version, str) or not behavior_version
                    or behavior_version != policy_version):
                raise LaunchError(
                    f"Online joined receipt {index} has an invalid behavior/policy version"
                )
            versions.add(behavior_version)
    if len(versions) != 1:
        raise LaunchError(
            f"Online joined receipts mix behavior adapter identities: {sorted(versions)}"
        )
    return next(iter(versions))


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LaunchError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise LaunchError(f"non-object JSONL row at {path}:{line_number}")
        rows.append(value)
    return rows


def publish_cumulative_ce_receipts(
    *, current: Path, destination: Path, wave_index: int, prior: Path | None,
) -> Path:
    current_rows = _jsonl_rows(current)
    if len(current_rows) != 20:
        raise LaunchError(f"current CE join output has {len(current_rows)} rows, expected 20")
    if wave_index == 1:
        if prior is not None:
            raise LaunchError("wave 1 cannot consume prior CE receipts")
        rows = current_rows
    else:
        if prior is None or not prior.is_file():
            raise LaunchError("wave 2+ requires the previous cumulative CE receipt file")
        prior_rows = _jsonl_rows(prior)
        if len(prior_rows) != 20 * (wave_index - 1):
            raise LaunchError("previous cumulative CE receipt count does not match wave index")
        rows = prior_rows + current_rows
    ids = [str(row.get("problem_id", "")) for row in rows]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise LaunchError("cumulative CE receipts contain missing or duplicate problem IDs")
    expected = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        for row in rows
    )
    if destination.exists():
        if destination.read_bytes() != expected:
            raise LaunchError(f"existing cumulative CE receipt file differs: {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("xb") as stream:
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    return destination


def verify_checkpoint_manifest(root: Path, name: str) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / name
    manifest = load_json(manifest_path)
    payload_hash = manifest.get("manifest_payload_sha256")
    if payload_hash != object_hash(manifest, "manifest_payload_sha256"):
        raise LaunchError(f"checkpoint manifest payload hash mismatch: {manifest_path}")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise LaunchError(f"checkpoint manifest lacks files: {manifest_path}")
    declared: set[str] = set()
    for record in files:
        if not isinstance(record, Mapping):
            raise LaunchError(f"malformed checkpoint file record: {manifest_path}")
        relative = str(record.get("path", ""))
        file_path = (root / relative).resolve()
        if not relative or root not in file_path.parents or relative in declared:
            raise LaunchError(f"unsafe/duplicate checkpoint path: {relative!r}")
        declared.add(relative)
        if (not file_path.is_file() or file_path.stat().st_size != int(record.get("size", -1))
                or sha256_file(file_path) != record.get("sha256")):
            raise LaunchError(f"checkpoint artifact is missing or changed: {file_path}")
    actual = {
        item.relative_to(root).as_posix() for item in root.rglob("*")
        if item.is_file() and item.name != name
    }
    if actual != declared:
        raise LaunchError(f"checkpoint file set differs from manifest: {root}")
    return manifest


def verify_ce_done(
    path: Path, *, wave_index: int, config_sha256: str, receipts_sha256: str,
) -> dict[str, Any]:
    done = load_json(path)
    expected = {
        "status": "complete", "wave_index": wave_index,
        "global_step": wave_index, "config_sha256": config_sha256,
        "receipts_sha256": receipts_sha256,
    }
    for key, value in expected.items():
        if done.get(key) != value:
            raise LaunchError(f"CE terminal receipt has wrong {key}: {done.get(key)!r}")
    checkpoint = Path(str(done.get("checkpoint", ""))).resolve()
    manifest = checkpoint / "checkpoint_manifest.json"
    if (not manifest.is_file()
            or sha256_file(manifest) != done.get("checkpoint_manifest_sha256")):
        raise LaunchError("CE checkpoint manifest is missing or changed")
    verify_checkpoint_manifest(checkpoint, "checkpoint_manifest.json")
    return done


def verify_online_done(path: Path, *, config_sha256: str) -> dict[str, Any]:
    done = load_json(path)
    receipt = done.get("update_receipt")
    if done.get("state") != "DONE" or not isinstance(receipt, Mapping):
        raise LaunchError("Online-v2 did not publish DONE")
    if done.get("config_sha256") != config_sha256 or receipt.get("accepted") is not True:
        raise LaunchError("Online-v2 terminal receipt is not an accepted update")
    if int(receipt.get("optimizer_steps_this_update", 0)) <= 0:
        raise LaunchError("Online-v2 accepted receipt has no optimizer step")
    if done.get("pre_policy_sha256") == done.get("post_policy_sha256"):
        raise LaunchError("Online-v2 accepted update did not change policy weights")
    checkpoint = done.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise LaunchError("Online-v2 DONE lacks checkpoint metadata")
    root = Path(str(checkpoint.get("path", ""))).resolve()
    manifest = root / "manifest.json"
    if not manifest.is_file() or sha256_file(manifest) != checkpoint.get("manifest_sha256"):
        raise LaunchError("Online-v2 checkpoint manifest is missing or changed")
    verify_checkpoint_manifest(root, "manifest.json")
    resume = checkpoint.get("resume_training_state")
    if not isinstance(resume, Mapping):
        raise LaunchError("Online-v2 checkpoint lacks resumable training state")
    require_file_pin(resume, "Online-v2 resume training state")
    adapter_models = list((root / "adapter").rglob("adapter_model.safetensors"))
    adapter_configs = list((root / "adapter").rglob("adapter_config.json"))
    if len(adapter_models) != 1 or len(adapter_configs) != 1:
        raise LaunchError("Online-v2 checkpoint must contain exactly one saved adapter")
    if not (root / "value_head.pt").is_file():
        raise LaunchError("Online-v2 checkpoint lacks value_head.pt")
    return done


def run_command(command: Sequence[str], *, env: Mapping[str, str], dry_run: bool) -> None:
    print(json.dumps({"event": "paired_train_command", "argv": list(command)},
                     ensure_ascii=False, sort_keys=True), flush=True)
    if not dry_run:
        subprocess.run(list(command), env=dict(env), check=True)


def resolve_predecessors(
    *, run_root: Path, wave_index: int, ce_resume: Path | None,
    online_adapter: Path | None, online_value_head: Path | None,
    online_resume_training_state: Path | None,
) -> tuple[Path | None, Path, Path, Path | None]:
    initial_adapter = EXPERIMENT_ROOT / "assets/initial_lora_r16_a32_seed20261004"
    initial_value = Path(
        "/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt"
    )
    if wave_index == 1:
        if ce_resume is not None or online_resume_training_state is not None:
            raise LaunchError("wave 1 cannot consume predecessor training state")
        return None, (online_adapter or initial_adapter), (online_value_head or initial_value), None

    previous = run_root / f"wave_{wave_index - 1:03d}"
    paired = load_json(previous / "PAIRED_TRAIN_DONE.json")
    if paired.get("schema_version") != DONE_SCHEMA or paired.get("state") != "DONE":
        raise LaunchError("previous paired training wave is not terminal DONE")
    ce_done_path = (previous / "ce" / "update" /
                    f"FORMAL_DONE.wave_{wave_index - 1:03d}.json")
    raw_ce_done = load_json(ce_done_path)
    ce_done = verify_ce_done(
        ce_done_path, wave_index=wave_index - 1,
        config_sha256=str(raw_ce_done.get("config_sha256", "")),
        receipts_sha256=str(raw_ce_done.get("receipts_sha256", "")),
    )
    online_done_path = previous / "online-v2" / "update" / "DONE.json"
    raw_online_done = load_json(online_done_path)
    online_done = verify_online_done(
        online_done_path, config_sha256=str(raw_online_done.get("config_sha256", ""))
    )
    checkpoint = online_done.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise LaunchError("previous Online-v2 DONE lacks checkpoint")
    online_root = Path(str(checkpoint.get("path", ""))).resolve()
    adapter_models = list((online_root / "adapter").rglob("adapter_model.safetensors"))
    if len(adapter_models) != 1:
        raise LaunchError("previous Online-v2 checkpoint has ambiguous adapter layout")
    inferred_adapter = adapter_models[0].parent
    resume = checkpoint.get("resume_training_state")
    if not isinstance(resume, Mapping):
        raise LaunchError("previous Online-v2 DONE lacks resume training state")
    return (
        ce_resume or Path(str(ce_done.get("checkpoint", ""))).resolve(),
        online_adapter or inferred_adapter,
        online_value_head or (online_root / "value_head.pt"),
        online_resume_training_state or require_file_pin(resume, "previous Online training state"),
    )


def _prepare_online_config(
    *, args: argparse.Namespace, manifest: Mapping[str, Any], config_path: Path,
    adapter: Path, value_head: Path, resume_state: Path | None, env: Mapping[str, str],
) -> str:
    if config_path.exists():
        return sha256_file(config_path)
    temporary = config_path.with_name(f".{config_path.name}.base-{os.getpid()}")
    pins = manifest["online_receipt_pins"]
    command = [
        args.python, str(EXPERIMENT_ROOT / "workstreams/online_v2_arm/scripts/prepare_real_one_update_config.py"),
        "--mode", "formal", "--model", str(args.model), "--adapter", str(adapter),
        "--value-head", str(value_head), "--target-repo", str(args.target_repo),
        "--behavior-adapter-name", joined_behavior_adapter_name(manifest),
        "--output", str(temporary),
    ]
    for pin in pins:
        command.extend(["--joined", str(pin["path"]),
                        "--signed-receipt", str(pin["source_receipt_path"])])
    run_command(command, env=env, dry_run=args.dry_run)
    if args.dry_run:
        return "DRY_RUN"
    config = load_json(temporary)
    if resume_state is not None:
        config["pins"]["resume_training_state"] = {
            "path": str(resume_state.resolve()), "sha256": sha256_file(resume_state)
        }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(config_path, config)
    temporary.unlink()
    return sha256_file(config_path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.wave_index <= 8:
        raise LaunchError("wave-index must be in 1..8")
    run_root = args.run_root.resolve()
    wave_root = run_root / f"wave_{args.wave_index:03d}"
    terminal_path = wave_root / "PAIRED_TRAIN_DONE.json"
    if terminal_path.is_file():
        terminal = load_json(terminal_path)
        if terminal.get("schema_version") != DONE_SCHEMA:
            raise LaunchError("existing paired terminal receipt has the wrong schema")
        return terminal

    ce_manifest = validate_join_manifest(args.ce_join_manifest.resolve(), args.wave_index)
    online_manifest = validate_join_manifest(args.online_join_manifest.resolve(), args.wave_index)
    ce_config = args.ce_config.resolve()
    if not ce_config.is_file():
        raise LaunchError(f"frozen CE config is missing: {ce_config}")
    ce_config_sha = sha256_file(ce_config)
    previous_cumulative = None
    if args.wave_index > 1:
        previous_cumulative = (
            run_root / f"wave_{args.wave_index - 1:03d}" / "control" /
            f"ce-receipts-through-wave-{args.wave_index - 1:03d}.jsonl"
        )
    cumulative = publish_cumulative_ce_receipts(
        current=require_file_pin(ce_manifest["ce_receipts"], "current CE receipts"),
        destination=wave_root / "control" /
        f"ce-receipts-through-wave-{args.wave_index:03d}.jsonl",
        wave_index=args.wave_index, prior=previous_cumulative,
    )
    cumulative_sha = sha256_file(cumulative)
    ce_resume, online_adapter, online_value, online_resume = resolve_predecessors(
        run_root=run_root, wave_index=args.wave_index, ce_resume=args.ce_resume,
        online_adapter=args.online_adapter, online_value_head=args.online_value_head,
        online_resume_training_state=args.online_resume_training_state,
    )
    for label, path in (("Online adapter", online_adapter), ("Online value head", online_value)):
        if not path.exists():
            raise LaunchError(f"{label} is missing: {path}")
    env = os.environ.copy()
    additions = [
        EXPERIMENT_ROOT / "workstreams/ce_arm/src",
        EXPERIMENT_ROOT / "workstreams/online_v2_arm/src",
    ]
    env["PYTHONPATH"] = os.pathsep.join(map(str, additions)) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONUNBUFFERED"] = "1"
    arm_order = args.arm_order
    if arm_order == "auto":
        arm_order = "ce-first" if args.wave_index % 2 else "online-first"
    state = {
        "schema_version": DONE_SCHEMA, "state": "RUNNING", "wave_index": args.wave_index,
        "arm_order": arm_order, "ce_join_manifest_sha256": sha256_file(args.ce_join_manifest),
        "online_join_manifest_sha256": sha256_file(args.online_join_manifest),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not args.dry_run:
        atomic_json(wave_root / "PAIRED_TRAIN_STATE.json", state)

    ce_run_dir = wave_root / "ce" / "update"
    ce_done_path = ce_run_dir / f"FORMAL_DONE.wave_{args.wave_index:03d}.json"
    online_root = wave_root / "online-v2"
    online_config = online_root / "config.json"
    online_output = online_root / "update"
    online_config_sha: str | None = None

    def run_ce() -> dict[str, Any]:
        if not ce_done_path.is_file():
            command = [
                args.python, "-m", "ce_arm.formal", "--config", str(ce_config),
                "--expected-config-sha256", ce_config_sha, "--receipts", str(cumulative),
                "--expected-receipts-sha256", cumulative_sha, "--run-dir", str(ce_run_dir),
                "--learner-dir", str(run_root / "ce-learner"),
                "--wave-index", str(args.wave_index),
            ]
            if ce_resume is not None:
                command.extend(["--resume", str(ce_resume)])
            run_command(command, env=env, dry_run=args.dry_run)
        if args.dry_run:
            return {"dry_run": True}
        return verify_ce_done(ce_done_path, wave_index=args.wave_index,
                              config_sha256=ce_config_sha, receipts_sha256=cumulative_sha)

    def run_online() -> dict[str, Any]:
        nonlocal online_config_sha
        online_config_sha = _prepare_online_config(
            args=args, manifest=online_manifest, config_path=online_config,
            adapter=online_adapter, value_head=online_value,
            resume_state=online_resume, env=env,
        )
        done = online_output / "DONE.json"
        if not done.is_file():
            command = [
                args.python,
                str(EXPERIMENT_ROOT / "workstreams/online_v2_arm/scripts/run_real_one_update.py"),
                "--config", str(online_config),
                "--expected-config-sha256", online_config_sha,
                "--output", str(online_output),
            ]
            if online_output.exists():
                command.append("--resume")
            run_command(command, env=env, dry_run=args.dry_run)
        if args.dry_run:
            return {"dry_run": True}
        return verify_online_done(done, config_sha256=online_config_sha)

    try:
        if arm_order == "ce-first":
            ce_done, online_done = run_ce(), run_online()
        else:
            online_done, ce_done = run_online(), run_ce()
        terminal = state | {
            "state": "DONE",
            "ce": {
                "terminal": str(ce_done_path.resolve()),
                "terminal_sha256": None if args.dry_run else sha256_file(ce_done_path),
                "checkpoint": ce_done.get("checkpoint"),
            },
            "online_v2": {
                "terminal": str((online_output / "DONE.json").resolve()),
                "terminal_sha256": (None if args.dry_run
                                    else sha256_file(online_output / "DONE.json")),
                "checkpoint": online_done.get("checkpoint"),
                "accepted": (online_done.get("update_receipt") or {}).get("accepted"),
            },
            "ce_cumulative_receipts": {
                "path": str(cumulative.resolve()), "sha256": cumulative_sha,
                "rows": 20 * args.wave_index,
            },
            "online_config_sha256": online_config_sha,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if not args.dry_run:
            atomic_json(terminal_path, terminal)
            atomic_json(wave_root / "PAIRED_TRAIN_STATE.json", terminal)
        return terminal
    except BaseException as exc:
        if not args.dry_run:
            failed = state | {
                "state": "FAILED", "error_type": type(exc).__name__, "error": str(exc),
                "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            atomic_json(wave_root / "PAIRED_TRAIN_FAILED.json", failed)
            atomic_json(wave_root / "PAIRED_TRAIN_STATE.json", failed)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--ce-join-manifest", type=Path, required=True)
    parser.add_argument("--online-join-manifest", type=Path, required=True)
    parser.add_argument("--ce-config", type=Path, default=(
        EXPERIMENT_ROOT / "workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json"
    ))
    parser.add_argument("--run-root", type=Path, default=(
        EXPERIMENT_ROOT / "runs/formal-20x10/seed-20261005"
    ))
    parser.add_argument("--model", type=Path,
                        default=Path("/mnt/workspace/models/REAL-Prover-fe76f68d"))
    parser.add_argument("--target-repo", type=Path,
                        default=EXPERIMENT_ROOT / "src/10-4-alpha-proof")
    parser.add_argument("--ce-resume", type=Path)
    parser.add_argument("--online-adapter", type=Path)
    parser.add_argument("--online-value-head", type=Path)
    parser.add_argument("--online-resume-training-state", type=Path)
    parser.add_argument("--arm-order", choices=("auto", "ce-first", "online-first"),
                        default="auto")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (LaunchError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"event": "paired_train_failed", "error": str(exc)},
                         ensure_ascii=False, sort_keys=True), flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

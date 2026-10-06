"""One-command signed-receipt -> real 7B CE update -> exact resume smoke."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .cli import build_replay_from_config
from .train import _atomic_json, sha256_file, validate_replay_bundle, verify_checkpoint


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _validate_smoke_config(config_path: Path, expected_hash: str) -> dict:
    actual = sha256_file(config_path)
    if actual != expected_hash:
        raise ValueError(f"config hash mismatch: expected {expected_hash}, got {actual}")
    config = _read_json(config_path)
    if config.get("status") != "frozen":
        raise ValueError("CE smoke requires a frozen, lock-bound config")
    if config.get("run_scope") != "real_7b_one_update_smoke":
        raise ValueError("CE smoke refuses a config outside real_7b_one_update_smoke scope")
    train = config.get("train", {})
    if train.get("steps_per_wave") != 1 or train.get("checkpoint_every_steps") != 1:
        raise ValueError("CE one-update smoke requires steps_per_wave=checkpoint_every_steps=1")
    if train.get("batch_size") != train.get("micro_batch_size"):
        raise ValueError("CE one-update smoke requires one memory-bounded micro-batch")
    return config


def _run_trainer(config: Path, config_hash: str, replay: Path, learner: Path,
                 wave_index: int, resume: Path | None = None) -> None:
    command = [
        sys.executable, "-m", "ce_arm.train",
        "--config", str(config),
        "--expected-config-sha256", config_hash,
        "--replay", str(replay),
        "--output", str(learner),
        "--wave-index", str(wave_index),
    ]
    if resume is not None:
        command.extend(["--resume", str(resume)])
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    subprocess.run(command, check=True, env=environment)


def run_smoke(*, config_path: str | Path, expected_config_sha256: str,
              receipt_path: str | Path, expected_receipt_sha256: str,
              run_dir: str | Path, wave_index: int = 1,
              verify_resume: bool = True) -> dict:
    config_path = Path(config_path).resolve()
    receipt_path = Path(receipt_path).resolve()
    run_dir = Path(run_dir).resolve()
    config = _validate_smoke_config(config_path, expected_config_sha256)
    train_variants = config.get("dataset", {}).get(
        "train_variants", config.get("dataset", {}).get("adaptation_variants")
    )
    if not isinstance(train_variants, list) or wave_index not in train_variants:
        raise ValueError("wave_index is outside the frozen dataset training variants")
    actual_receipt_hash = sha256_file(receipt_path)
    if actual_receipt_hash != expected_receipt_sha256:
        raise ValueError(
            f"signed join receipt hash mismatch: expected {expected_receipt_sha256}, "
            f"got {actual_receipt_hash}"
        )

    run_dir.mkdir(parents=True, exist_ok=True)
    replay = run_dir / "replay"
    learner = run_dir / "learner"
    if not replay.exists():
        build_replay_from_config(
            config_path, receipt_path, replay, wave_index,
            expected_config_sha256=expected_config_sha256,
        )
    transitions, replay_manifest, replay_hash = validate_replay_bundle(
        replay, config, wave_index, expected_config_sha256
    )
    if replay_manifest.get("source_receipts_sha256") != expected_receipt_sha256:
        raise ValueError("existing replay was built from a different signed join receipt")

    started = time.time()
    _run_trainer(config_path, expected_config_sha256, replay, learner, wave_index)
    latest_path = learner / "latest.json"
    latest = _read_json(latest_path)
    checkpoint = Path(latest["checkpoint"]).resolve()
    verified = verify_checkpoint(checkpoint)
    if verify_resume:
        # A second process reloads the saved adapter, value head, optimizer and
        # RNG state.  Since the frozen target is already step 1, it performs no
        # extra optimizer update and proves exact idempotent recovery.
        _run_trainer(
            config_path, expected_config_sha256, replay, learner, wave_index,
            resume=checkpoint,
        )
        latest_after = _read_json(latest_path)
        if latest_after != latest:
            raise ValueError("resume verification changed the committed checkpoint")
        verify_checkpoint(checkpoint)

    done_path = learner / f"DONE.wave_{wave_index:03d}.json"
    learner_done = _read_json(done_path)
    if learner_done.get("global_step") != 1:
        raise ValueError("one-update smoke did not finish at global_step=1")
    result = {
        "schema_version": "fate-m.ce-real-7b-one-update-smoke.v1",
        "status": "complete",
        "config": str(config_path),
        "config_sha256": expected_config_sha256,
        "signed_join_receipt": str(receipt_path),
        "signed_join_receipt_sha256": expected_receipt_sha256,
        "wave_index": wave_index,
        "replay": str(replay),
        "replay_manifest_sha256": replay_hash,
        "transitions_sha256": sha256_file(transitions),
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": sha256_file(checkpoint / "checkpoint_manifest.json"),
        "checkpoint_manifest_payload_sha256": verified["manifest_payload_sha256"],
        "global_step": 1,
        "resume_verified": verify_resume,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    _atomic_json(run_dir / "SMOKE_DONE.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--receipt", required=True, help="join output ce-receipt.json or JSONL")
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--wave-index", type=int, default=1)
    parser.add_argument("--skip-resume-verification", action="store_true")
    args = parser.parse_args(argv)
    result = run_smoke(
        config_path=args.config,
        expected_config_sha256=args.expected_config_sha256,
        receipt_path=args.receipt,
        expected_receipt_sha256=args.expected_receipt_sha256,
        run_dir=args.run_dir,
        wave_index=args.wave_index,
        verify_resume=not args.skip_resume_verification,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

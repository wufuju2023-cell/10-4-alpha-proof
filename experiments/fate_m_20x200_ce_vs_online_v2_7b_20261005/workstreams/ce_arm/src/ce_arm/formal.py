"""Run one frozen subset_20x10 CE wave through the accepted replay/trainer path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cli import build_replay_from_config
from .replay import sha256_file
from .train import _atomic_json, run as train_wave, verify_checkpoint


def run_formal_wave(*, config_path: str | Path, expected_config_sha256: str,
                    receipts: str | Path, expected_receipts_sha256: str,
                    run_dir: str | Path, learner_dir: str | Path, wave_index: int,
                    resume: str | Path | None = None) -> dict:
    config_path = Path(config_path).resolve()
    receipts = Path(receipts).resolve()
    run_dir = Path(run_dir).resolve()
    learner_dir = Path(learner_dir).resolve()
    if sha256_file(config_path) != expected_config_sha256:
        raise ValueError("frozen CE config hash mismatch")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("status") != "frozen" or config.get("run_scope") != "subset_20x10_formal":
        raise ValueError("formal wave requires a frozen subset_20x10_formal config")
    if not 1 <= wave_index <= 8:
        raise ValueError("subset formal wave_index must be in 1..8")
    if sha256_file(receipts) != expected_receipts_sha256:
        raise ValueError("cumulative signed receipt bundle hash mismatch")
    if wave_index > 1 and resume is None:
        raise ValueError("waves 2..8 require the preceding CE checkpoint")

    replay = run_dir / "replay"
    if not replay.exists():
        build_replay_from_config(
            config_path, receipts, replay, wave_index,
            expected_config_sha256=expected_config_sha256,
        )
    done = train_wave(
        config_path, replay, learner_dir, wave_index=wave_index,
        resume_checkpoint=resume, expected_config_sha256=expected_config_sha256,
    )
    if int(done.get("global_step", -1)) != wave_index:
        raise ValueError("CE formal checkpoint global_step does not equal completed wave count")
    checkpoint = Path(done["checkpoint"]).resolve()
    verify_checkpoint(checkpoint)
    result = {
        "schema_version": "fate-m.ce-subset-20x10-wave.v1",
        "status": "complete",
        "wave_index": wave_index,
        "config_sha256": expected_config_sha256,
        "receipts_sha256": expected_receipts_sha256,
        "replay": str(replay),
        "learner": str(learner_dir),
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": sha256_file(checkpoint / "checkpoint_manifest.json"),
        "global_step": wave_index,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(run_dir / f"FORMAL_DONE.wave_{wave_index:03d}.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--expected-receipts-sha256", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--learner-dir", required=True)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--resume")
    args = parser.parse_args(argv)
    result = run_formal_wave(
        config_path=args.config, expected_config_sha256=args.expected_config_sha256,
        receipts=args.receipts, expected_receipts_sha256=args.expected_receipts_sha256,
        run_dir=args.run_dir, learner_dir=args.learner_dir, wave_index=args.wave_index,
        resume=args.resume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

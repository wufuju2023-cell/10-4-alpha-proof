from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_paired_train_wave.py"
SPEC = importlib.util.spec_from_file_location("run_paired_train_wave", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipts(path: Path, wave: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"problem_id": f"fate_m_{family:03d}_v{wave:03d}",
         "request": {"wave_index": wave}}
        for family in range(1, 21)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_cumulative_ce_receipts_advance_exactly_one_wave(tmp_path: Path) -> None:
    first = tmp_path / "w1.jsonl"
    second = tmp_path / "w2.jsonl"
    _receipts(first, 1)
    _receipts(second, 2)
    cumulative1 = MODULE.publish_cumulative_ce_receipts(
        current=first, destination=tmp_path / "cum1.jsonl", wave_index=1, prior=None
    )
    cumulative2 = MODULE.publish_cumulative_ce_receipts(
        current=second, destination=tmp_path / "cum2.jsonl", wave_index=2,
        prior=cumulative1,
    )
    assert len(cumulative2.read_text(encoding="utf-8").splitlines()) == 40


def test_cumulative_ce_receipts_reject_duplicate_problem(tmp_path: Path) -> None:
    current = tmp_path / "current.jsonl"
    _receipts(current, 1)
    with pytest.raises(MODULE.LaunchError, match="duplicate"):
        MODULE.publish_cumulative_ce_receipts(
            current=current, destination=tmp_path / "bad.jsonl", wave_index=2,
            prior=current,
        )


def test_online_done_requires_accepted_weight_changing_update(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    for relative in ("adapter/adapter_model.safetensors", "adapter/adapter_config.json",
                     "value_head.pt", "training_state.pt"):
        path = checkpoint / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    files = []
    for path in sorted(item for item in checkpoint.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(checkpoint).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha(path),
        })
    manifest = {"files": files}
    manifest["manifest_payload_sha256"] = MODULE.object_hash(manifest)
    _json(checkpoint / "manifest.json", manifest)
    config_sha = "a" * 64
    done = {
        "state": "DONE", "config_sha256": config_sha,
        "update_receipt": {"accepted": True, "optimizer_steps_this_update": 1},
        "pre_policy_sha256": "b" * 64, "post_policy_sha256": "c" * 64,
        "checkpoint": {
            "path": str(checkpoint),
            "manifest_sha256": _sha(checkpoint / "manifest.json"),
            "resume_training_state": {
                "path": str(checkpoint / "training_state.pt"),
                "sha256": _sha(checkpoint / "training_state.pt"),
            },
        },
    }
    terminal = tmp_path / "DONE.json"
    _json(terminal, done)
    assert MODULE.verify_online_done(terminal, config_sha256=config_sha)["state"] == "DONE"
    done["post_policy_sha256"] = done["pre_policy_sha256"]
    _json(terminal, done)
    with pytest.raises(MODULE.LaunchError, match="did not change"):
        MODULE.verify_online_done(terminal, config_sha256=config_sha)


def test_join_manifest_checks_all_twenty_pins(tmp_path: Path) -> None:
    ce = tmp_path / "ce.jsonl"
    _receipts(ce, 1)
    pins = []
    sessions = []
    for family in range(1, 21):
        session = f"fate_m_{family:03d}_v001"
        sessions.append(session)
        joined = tmp_path / f"{session}.joined.json"
        signed = tmp_path / f"{session}.signed.json"
        _json(joined, {"session": session})
        _json(signed, {"problem_id": session})
        pins.append({
            "path": str(joined), "sha256": _sha(joined),
            "source_receipt_path": str(signed),
            "source_receipt_file_sha256": _sha(signed),
        })
    pin_file = tmp_path / "pins.json"
    _json(pin_file, pins)
    manifest = tmp_path / "manifest.json"
    _json(manifest, {
        "schema_version": MODULE.JOIN_SCHEMA, "status": "complete", "wave_index": 1,
        "sessions": sessions,
        "ce_receipts": {"path": str(ce), "sha256": _sha(ce)},
        "online_receipt_pins_file": {"path": str(pin_file), "sha256": _sha(pin_file)},
        "online_receipt_pins": pins,
    })
    assert len(MODULE.validate_join_manifest(manifest, 1)["sessions"]) == 20


def test_behavior_adapter_name_is_recovered_from_all_joined_receipts(
    tmp_path: Path,
) -> None:
    pins = []
    for family in range(1, 21):
        joined = tmp_path / f"joined-{family:03d}.json"
        _json(joined, {
            "searches": [{
                "policy_version": "shared-wave-001-input",
                "behavior_version": "shared-wave-001-input",
            }],
        })
        pins.append({"path": str(joined), "sha256": _sha(joined)})
    manifest = {"online_receipt_pins": pins}
    assert MODULE.joined_behavior_adapter_name(manifest) == "shared-wave-001-input"

    changed = json.loads(joined.read_text(encoding="utf-8"))
    changed["searches"][0]["behavior_version"] = "different-wave"
    changed["searches"][0]["policy_version"] = "different-wave"
    _json(joined, changed)
    pins[-1]["sha256"] = _sha(joined)
    with pytest.raises(MODULE.LaunchError, match="mix behavior adapter identities"):
        MODULE.joined_behavior_adapter_name(manifest)

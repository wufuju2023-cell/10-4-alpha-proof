import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ce_arm.smoke as smoke
from ce_arm.cli import bind_smoke_config
from ce_arm.locks import generate_tokenizer_lock
from ce_arm.train import object_hash, sha256_file


def _frozen_smoke_config(path: Path) -> str:
    template = json.loads((ROOT / "config" / "ce_arm.real_7b_one_update.template.json").read_text())
    template["status"] = "frozen"
    path.write_text(json.dumps(template), encoding="utf-8")
    return sha256_file(path)


def test_bind_smoke_config_hash_pins_every_lock(tmp_path):
    names = (
        "asset_lock", "course_manifest", "shared_actor_config",
        "shared_budget_config", "tokenizer_lock", "trusted_verifier_lock",
    )
    model = tmp_path / "model"
    model.mkdir()
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    template_value = json.loads(
        (ROOT / "config" / "ce_arm.real_7b_one_update.template.json").read_text()
    )
    template_value["model"]["base_path"] = str(model)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(template_value), encoding="utf-8")
    paths = {}
    for name in names:
        path = tmp_path / f"{name}.json"
        payload = {"name": name}
        if name == "trusted_verifier_lock":
            payload["verifier_id"] = "lean428-test"
        if name == "tokenizer_lock":
            generate_tokenizer_lock(model, "1" * 40, path)
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "ce-smoke.frozen.json"
    result = bind_smoke_config(
        template_path, output, paths
    )
    frozen = json.loads(output.read_text())
    assert result["sha256"] == sha256_file(output)
    assert frozen["status"] == "frozen"
    assert frozen["locks"]["trusted_verifier_lock"]["verifier_id"] == "lean428-test"
    assert frozen["locks"]["shared_actor_config"]["receipt_identity_sha256"] != frozen[
        "locks"
    ]["shared_actor_config"]["sha256"]
    assert frozen["locks"]["tokenizer_lock"]["receipt_identity_sha256"] != frozen[
        "locks"
    ]["tokenizer_lock"]["sha256"]
    for name, path in paths.items():
        assert frozen["locks"][name]["sha256"] == sha256_file(path)


def test_smoke_guard_rejects_multi_update_config(tmp_path):
    path = tmp_path / "config.json"
    config_hash = _frozen_smoke_config(path)
    value = json.loads(path.read_text())
    value["train"]["steps_per_wave"] = 2
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="one-update"):
        smoke._validate_smoke_config(path, sha256_file(path))
    with pytest.raises(ValueError, match="config hash mismatch"):
        smoke._validate_smoke_config(path, config_hash)


def test_launcher_builds_once_then_reloads_exact_checkpoint_without_second_update(
        tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config_hash = _frozen_smoke_config(config)
    receipt = tmp_path / "ce-receipt.json"
    receipt.write_text(json.dumps({"schema_version": 2}, indent=2), encoding="utf-8")
    receipt_hash = sha256_file(receipt)
    run_dir = tmp_path / "run"
    calls = []

    def fake_build(config_path, receipt_path, replay, wave, **kwargs):
        replay = Path(replay)
        replay.mkdir()
        (replay / "transitions.jsonl").write_text("{}\n", encoding="utf-8")
        (replay / "manifest.json").write_text("{}", encoding="utf-8")

    replay_manifest = {
        "source_receipts_sha256": receipt_hash,
        "manifest_payload_sha256": "a" * 64,
    }

    def fake_validate(replay, config_value, wave, expected_hash):
        return Path(replay) / "transitions.jsonl", replay_manifest, "b" * 64

    def fake_train(config_path, expected_hash, replay, learner, wave, resume=None):
        calls.append(resume)
        learner = Path(learner)
        checkpoint = learner / "checkpoints" / "wave_001_step_000001_global_00000001"
        checkpoint.mkdir(parents=True, exist_ok=True)
        manifest = {"status": "complete"}
        manifest["manifest_payload_sha256"] = object_hash(manifest)
        (checkpoint / "checkpoint_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (learner / "latest.json").write_text(json.dumps({
            "checkpoint": str(checkpoint.resolve()),
            "checkpoint_manifest_sha256": sha256_file(checkpoint / "checkpoint_manifest.json"),
            "wave_index": 1, "wave_step": 1, "global_step": 1,
            "verified_manifest_payload_sha256": manifest["manifest_payload_sha256"],
        }), encoding="utf-8")
        (learner / "DONE.wave_001.json").write_text(json.dumps({"global_step": 1}), encoding="utf-8")

    monkeypatch.setattr(smoke, "build_replay_from_config", fake_build)
    monkeypatch.setattr(smoke, "validate_replay_bundle", fake_validate)
    monkeypatch.setattr(smoke, "_run_trainer", fake_train)
    monkeypatch.setattr(smoke, "verify_checkpoint", lambda path: {
        "manifest_payload_sha256": json.loads(
            (Path(path) / "checkpoint_manifest.json").read_text()
        )["manifest_payload_sha256"]
    })

    result = smoke.run_smoke(
        config_path=config, expected_config_sha256=config_hash,
        receipt_path=receipt, expected_receipt_sha256=receipt_hash,
        run_dir=run_dir,
    )
    assert calls[0] is None
    assert calls[1] == Path(result["checkpoint"])
    assert result["global_step"] == 1
    assert result["resume_verified"] is True
    assert (run_dir / "SMOKE_DONE.json").is_file()

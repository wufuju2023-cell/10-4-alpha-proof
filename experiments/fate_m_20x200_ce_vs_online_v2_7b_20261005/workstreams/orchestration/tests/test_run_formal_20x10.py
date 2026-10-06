from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_formal_20x10.py"
SPEC = importlib.util.spec_from_file_location("run_formal_20x10", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_plan_closes_frozen_20x10_lifecycle():
    plan = module.build_plan()
    assert len(plan) == 60
    assert plan[0]["id"] == "baseline_eval"
    assert plan[-2]["id"] == "ce_final_eval"
    assert plan[-1]["id"] == "online_v2_final_eval"
    ids = [unit["id"] for unit in plan]
    assert len(ids) == len(set(ids))
    for wave in range(1, 9):
        for arm in ("ce", "online_v2"):
            assert [f"w{wave:03d}_{arm}_{kind}" for kind in ("rollout", "join", "update")] == [
                unit["id"] for unit in plan
                if unit["wave"] == wave and unit["arm"] == arm and unit["split"] == "train"
            ]
        assert f"w{wave:03d}_common_commit" in ids
    assert ids.index("lock_final_checkpoints") < ids.index("ce_final_eval")


def test_template_is_intentionally_not_launchable(tmp_path):
    template = SCRIPT.parents[1] / "config" / "formal_20x10.production.template.json"
    protocol = SCRIPT.parents[3] / "config" / "subset_20x10_protocol.frozen.json"
    with pytest.raises(module.FormalRunError, match="status=frozen"):
        module.validate_config(template, protocol, 21600)


def test_only_frozen_deadline_is_accepted():
    template = SCRIPT.parents[1] / "config" / "formal_20x10.production.template.json"
    protocol = SCRIPT.parents[3] / "config" / "subset_20x10_protocol.frozen.json"
    with pytest.raises(module.FormalRunError):
        module.validate_config(template, protocol, 21599)


def test_rollout_receipt_requires_real_protocol_limits(tmp_path):
    unit = {"id": "w001_ce_rollout", "kind": "rollout", "arm": "ce",
            "wave": 1, "split": "train"}
    artifact = tmp_path / "rollout.json"
    artifact.write_text("{}\n", encoding="utf-8")
    receipt = {
        "schema_version": module.RECEIPT_SCHEMA, "status": "complete",
        "unit_id": unit["id"], "kind": unit["kind"], "arm": unit["arm"],
        "wave": unit["wave"], "split": unit["split"],
        "protocol_sha256": "a" * 64, "config_sha256": "b" * 64,
        "metrics": {"problems": 20, "max_attempts_per_problem": 1,
                    "max_new_tokens_per_attempt": 256},
        "artifacts": [{"role": "rollout_manifest", "path": str(artifact),
                       "sha256": module.sha256_file(artifact)}],
    }
    path = tmp_path / "UNIT_DONE.json"
    module.atomic_json(path, receipt)
    with pytest.raises(module.FormalRunError, match="four attempts / 512"):
        module.validate_unit_receipt(path, unit, "a" * 64, "b" * 64)


def test_resume_revalidates_artifact_bytes(tmp_path):
    unit = {"id": "w001_online_v2_update", "kind": "update", "arm": "online_v2",
            "wave": 1, "split": "train"}
    checkpoint = tmp_path / "checkpoint.json"
    update = tmp_path / "update.json"
    checkpoint.write_text("{}\n", encoding="utf-8")
    update.write_text("{}\n", encoding="utf-8")
    receipt = {
        "schema_version": module.RECEIPT_SCHEMA, "status": "complete",
        "unit_id": unit["id"], "kind": unit["kind"], "arm": unit["arm"],
        "wave": unit["wave"], "split": unit["split"],
        "protocol_sha256": "a" * 64, "config_sha256": "b" * 64,
        "source_join_receipt_sha256": "c" * 64,
        "metrics": {"optimizer_steps": 1},
        "artifacts": [
            {"role": "checkpoint", "path": str(checkpoint),
             "sha256": module.sha256_file(checkpoint)},
            {"role": "update_receipt", "path": str(update),
             "sha256": module.sha256_file(update)},
        ],
    }
    path = tmp_path / "UNIT_DONE.json"
    module.atomic_json(path, receipt)
    module.validate_unit_receipt(path, unit, "a" * 64, "b" * 64)
    checkpoint.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(module.FormalRunError, match="artifact differs"):
        module.validate_unit_receipt(path, unit, "a" * 64, "b" * 64)

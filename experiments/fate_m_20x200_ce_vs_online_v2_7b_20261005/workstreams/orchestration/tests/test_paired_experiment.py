from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experiment_orchestrator import (  # noqa: E402
    atomic_json, find_stage, load_json, run_stage, validate_config,
    write_storage_observation,
)
from paired_experiment import ProtocolError, freeze_protocol, validate_protocol  # noqa: E402
from paired_stage_worker import execute  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PairedExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        for name in ("course", "adapter", "actor", "ce-config", "online-config"):
            (self.root / f"{name}.json").write_text(
                json.dumps({"name": name}) + "\n", encoding="utf-8"
            )
        (self.root / "receipts.json").write_text(
            json.dumps({"receipt_sha256": "a" * 64}) + "\n", encoding="utf-8"
        )
        (self.root / "joined.json").write_text(
            json.dumps({"source_receipt_sha256": "a" * 64}) + "\n", encoding="utf-8"
        )
        (self.root / "ce-config.json").write_text(json.dumps({
            "train": {"seed": 20261004},
            "model": {"initial_adapter": {"path": str(self.root)}},
            "locks": {
                "course_manifest": {"path": str(self.root / "course.json")},
                "shared_actor_config": {"path": str(self.root / "actor.json")},
            },
        }) + "\n", encoding="utf-8")
        (self.root / "online-config.json").write_text(json.dumps({
            "train": {"seed": 20261004},
            "pins": {"adapter": {"path": str(self.root)},
                     "receipts": [{
                         "path": str(self.root / "joined.json"),
                         "sha256": digest(self.root / "joined.json"),
                         "source_receipt_path": str(self.root / "receipts.json"),
                         "source_receipt_file_sha256": digest(self.root / "receipts.json"),
                         "source_receipt_sha256": "a" * 64,
                     }],},
        }) + "\n", encoding="utf-8")
        self.native = self.root / "native.py"
        self.native.write_text(
            "import argparse, json\n"
            "from pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--output', required=True); "
            "p.add_argument('--fail-once', action='store_true'); p.add_argument('--resume'); a=p.parse_args()\n"
            "o=Path(a.output); o.mkdir(parents=True, exist_ok=True); c=o/'native.ckpt'; c.write_text('ok')\n"
            "(o/'latest.json').write_text(json.dumps({'checkpoint':str(c.resolve())}))\n"
            "m=o/'first_failed'\n"
            "if a.fail_once and not a.resume and not m.exists(): m.write_text('1'); raise SystemExit(7)\n"
            "(o/'terminal.json').write_text(json.dumps({'state':'DONE'}))\n",
            encoding="utf-8",
        )
        self.protocol_path = self.root / "protocol.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pin(self, name: str) -> dict[str, str]:
        path = self.root / f"{name}.json"
        return {"path": str(path), "sha256": digest(path)}

    def stage(self, arm: str, *, fail_once: bool = False) -> dict:
        stage_id = f"smoke_w001_{arm}"
        native = self.root / "native-output" / arm
        command = [sys.executable, str(self.native), "--output", "{native_output}"]
        if fail_once:
            command.append("--fail-once")
        return {
            "id": stage_id, "arm": arm, "mode": "smoke", "wave_index": 1,
            "arm_config": self.pin("ce-config" if arm == "ce" else "online-config"),
            "command": command, "cwd": str(self.root),
            "native_output_dir": str(native),
            "native_terminal": str(native / "terminal.json"),
            "native_checkpoint": str(native / "native.ckpt"),
            "native_resume_pointer": {
                "path": str(native / "latest.json"), "json_key": "checkpoint"
            },
            "resume_command_append": ["--resume", "{native_checkpoint}"],
            "budget_seconds": 60, "max_additional_gib": 0.1,
            "control_dir": str(self.root / "control" / stage_id),
            "progress_file": str(self.root / "control" / stage_id / "progress.json"),
            "terminal_status_json_key": "state", "terminal_success_values": ["DONE"],
        }

    def protocol(self, *, fail_once: bool = False) -> dict:
        return {
            "schema_version": 1, "experiment_id": "paired-test",
            "experiment_root": str(self.root), "run_root": str(self.root / "run"),
            "emergency_dir": str(self.root / "emergency"), "seed": 20261004,
            "execution": "sequential_single_gpu",
            "storage_guard": {"observation_file": str(self.root / "storage.json"),
                              "expected_capacity_gib": 100, "max_age_seconds": 45,
                              "warn_used_gib": 90, "stop_used_gib": 95},
            "shared_inputs": {"course": self.pin("course"),
                              "initial_adapter": self.pin("adapter"),
                              "live_receipts": self.pin("receipts"),
                              "actor_config": self.pin("actor")},
            "frozen_environment": {"inherit": ["PATH"],
                                   "set": {"PYTHONUNBUFFERED": "1"}},
            "stages": [self.stage("ce", fail_once=fail_once), self.stage("online_v2")],
        }

    def write_protocol(self, value: dict) -> None:
        self.protocol_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_protocol_requires_complete_fair_pair(self) -> None:
        value = self.protocol()
        value["stages"] = value["stages"][:1]
        with self.assertRaisesRegex(ProtocolError, "one CE and one Online-v2"):
            validate_protocol(value)

    def test_freeze_generates_reviewed_orchestrator_contract(self) -> None:
        self.write_protocol(self.protocol())
        output = self.root / "orchestrator.json"
        report = freeze_protocol(self.protocol_path, output)
        config = validate_config(load_json(output))
        self.assertEqual(report["execution"], "sequential_single_gpu")
        self.assertEqual([stage["id"] for stage in config["stages"]],
                         ["smoke_w001_ce", "smoke_w001_online_v2"])
        self.assertTrue(all(stage["resume_contract"]["mode"] == "checkpoint"
                            for stage in config["stages"]))
        ids = {item["id"] for item in config["immutable_inputs"]}
        self.assertTrue({"shared_course", "shared_initial_adapter", "shared_live_receipts",
                         "shared_actor_config", "paired_stage_worker"}.issubset(ids))

    def test_freeze_rejects_different_arm_seed(self) -> None:
        online = json.loads((self.root / "online-config.json").read_text(encoding="utf-8"))
        online["train"]["seed"] = 999
        (self.root / "online-config.json").write_text(json.dumps(online) + "\n", encoding="utf-8")
        self.write_protocol(self.protocol())
        with self.assertRaisesRegex(ProtocolError, "train.seed differs"):
            freeze_protocol(self.protocol_path, self.root / "orchestrator.json")

    def test_freeze_rejects_online_receipt_from_different_canonical_source(self) -> None:
        online = json.loads((self.root / "online-config.json").read_text(encoding="utf-8"))
        online["pins"]["receipts"][0]["source_receipt_sha256"] = "b" * 64
        (self.root / "online-config.json").write_text(json.dumps(online) + "\n", encoding="utf-8")
        self.write_protocol(self.protocol())
        with self.assertRaisesRegex(ProtocolError, "canonical source receipt hash differs"):
            freeze_protocol(self.protocol_path, self.root / "orchestrator.json")

    def test_freeze_rejects_joined_payload_from_different_source(self) -> None:
        (self.root / "joined.json").write_text(
            json.dumps({"source_receipt_sha256": "b" * 64}) + "\n", encoding="utf-8"
        )
        online = json.loads((self.root / "online-config.json").read_text(encoding="utf-8"))
        online["pins"]["receipts"][0]["sha256"] = digest(self.root / "joined.json")
        (self.root / "online-config.json").write_text(json.dumps(online) + "\n", encoding="utf-8")
        self.write_protocol(self.protocol())
        with self.assertRaisesRegex(ProtocolError, "joined payload is not derived"):
            freeze_protocol(self.protocol_path, self.root / "orchestrator.json")

    def test_worker_failure_then_resume_preserves_unit_zero_and_commits_one(self) -> None:
        self.write_protocol(self.protocol(fail_once=True))
        stage = "smoke_w001_ce"
        env = {"EXPERIMENT_STAGE_ID": stage,
               "EXPERIMENT_INPUT_FINGERPRINT": "a" * 64,
               "EXPERIMENT_STAGE_FINGERPRINT": "b" * 64}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(execute(self.protocol_path, stage, None), 7)
            control = self.root / "control" / stage
            unit0 = (control / "checkpoint_receipt.json").read_bytes()
            self.assertEqual(execute(self.protocol_path, stage, control / "checkpoint.json"), 0)
        receipt = load_json(control / "checkpoint_receipt.json")
        self.assertEqual(receipt["last_committed_unit"], 1)
        self.assertNotEqual(unit0, (control / "checkpoint_receipt.json").read_bytes())
        result = load_json(control / "result.json")
        self.assertEqual((result["arm"], result["status"], result["seed"]),
                         ("ce", "DONE", 20261004))
        self.assertEqual(load_json(control / "success_receipt.json")["outputs"]["result"]["sha256"],
                         digest(control / "result.json"))

    def test_frozen_pair_runs_through_real_supervisor_contract(self) -> None:
        self.write_protocol(self.protocol())
        output = self.root / "orchestrator.json"
        freeze_protocol(self.protocol_path, output)
        config = load_json(output)
        config["stages"][0]["command"][0] = sys.executable
        atomic_json(output, config)
        validate_config(config)
        write_storage_observation(
            self.root / "storage.json", used_gib=10, capacity_gib=100,
            source="modelscope_ui_top_right",
        )
        stage = find_stage(config, "smoke_w001_ce")
        self.assertEqual(run_stage(output, config, stage, resume=False), 0)
        state = load_json(self.root / "run" / "stages" / "smoke_w001_ce" / "state.json")
        self.assertEqual(state["status"], "DONE")
        self.assertEqual(state["checkpoint_evidence"]["last_committed_unit"], 1)


if __name__ == "__main__":
    unittest.main()

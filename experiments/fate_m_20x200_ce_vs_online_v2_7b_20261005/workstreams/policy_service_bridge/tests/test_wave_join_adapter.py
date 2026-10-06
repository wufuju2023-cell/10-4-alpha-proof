from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble_wave_join_inputs import assemble, sha256_file  # noqa: E402


class WaveJoinAssemblyTests(unittest.TestCase):
    def test_assembles_exact_twenty_same_source_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks = [{"session_id": f"fate_m_{index:03d}_v002"}
                     for index in range(1, 21)]
            plan = root / "plan.json"
            plan.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
            joins = root / "joins"
            for task in tasks:
                session = task["session_id"]
                joined = joins / session
                (joined / "strict-replay").mkdir(parents=True)
                signed = {
                    "problem_id": session, "receipt_sha256": session.ljust(64, "0")[:64],
                    "request": {"wave_index": 2},
                    "behavior_identity": {"behavior_sha256": "a" * 64},
                }
                (joined / "strict-replay" / "receipt.json").write_text(
                    json.dumps(signed), encoding="utf-8")
                (joined / "ce-receipt.json").write_text(
                    json.dumps(signed), encoding="utf-8")
                (joined / "online-v2-receipts.json").write_text(json.dumps({
                    "source_receipt_sha256": signed["receipt_sha256"]
                }), encoding="utf-8")

            output = root / "inputs"
            manifest = assemble(plan_path=plan, join_root=joins,
                                output_dir=output, wave_index=2)

            self.assertEqual(manifest["wave_index"], 2)
            self.assertEqual(len(manifest["online_receipt_pins"]), 20)
            self.assertEqual(
                manifest["online_receipt_pins_file"]["sha256"],
                sha256_file(output / "online-v2-receipt-pins.json"),
            )
            self.assertEqual(len((output / "ce-receipts.jsonl").read_text(
                encoding="utf-8").splitlines()), 20)

    def test_rejects_plan_from_another_wave_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "plan.json"
            plan.write_text(json.dumps({"tasks": [
                {"session_id": f"fate_m_{index:03d}_v001"} for index in range(1, 21)
            ]}), encoding="utf-8")
            output = root / "inputs"
            with self.assertRaisesRegex(ValueError, "wave-index"):
                assemble(plan_path=plan, join_root=root / "joins",
                         output_dir=output, wave_index=2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

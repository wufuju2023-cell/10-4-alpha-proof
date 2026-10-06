from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PrepareRuntimeToolResolutionTests(unittest.TestCase):
    def test_resolution_priority_and_fail_closed_behavior(self) -> None:
        completed = subprocess.run(
            ["bash", "./tests/test_resolve_lean_tools.sh"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("resolve-lean-tools: PASS", completed.stdout)

    def test_prepare_runtime_uses_resolved_lake_everywhere(self) -> None:
        source = (ROOT / "scripts" / "prepare_runtime.sh").read_text(encoding="utf-8")
        self.assertIn('lake_bin="$(fate_resolve_lake_bin "$experiment_root")"', source)
        self.assertNotIn("&& lake build", source)
        self.assertNotIn("&& lake update", source)
        self.assertIn('--lake-bin "$lake_bin"', source)


if __name__ == "__main__":
    unittest.main()

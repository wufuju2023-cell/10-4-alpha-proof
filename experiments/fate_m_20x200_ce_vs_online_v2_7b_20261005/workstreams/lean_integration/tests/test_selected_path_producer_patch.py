from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


WORKSTREAM = Path(__file__).resolve().parents[1]
BASELINE = WORKSTREAM / "runtime" / "overlay" / "Reap" / "Training" / "RolloutSink.lean"
PATCH_0004 = WORKSTREAM / "runtime" / "patches" / "0004-canonical-candidate-provenance.patch"
PATCH_0005 = WORKSTREAM / "runtime" / "patches" / "0005-canonical-selected-path-producer.patch"
PREPARE = WORKSTREAM / "scripts" / "prepare_runtime.sh"
BASELINE_SHA256 = "b9ee4243f972b0e5b1c7aecc9a2541e73c0d5ee5d491b168dec5952ad0a149c5"
NORMALIZED_SHA256 = "fc5dd2f31cba1ff26caef7a9c205d9c17c910fc0ae5258cf2e15cec213aa1379"
POST_PATCH_SHA256 = "3be945d506760552886d294e8f334bc2e02640278e6fcce96de3c62206d490d3"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_rollout_sink(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


class SelectedPathProducerPatchTests(unittest.TestCase):
    def test_patch_applies_and_emits_only_after_verified_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "Reap" / "Training" / "RolloutSink.lean"
            target.parent.mkdir(parents=True)
            shutil.copyfile(BASELINE, target)
            original = target.read_bytes()
            self.assertEqual(file_sha256(target), BASELINE_SHA256)
            self.assertIn(b"\r\n", original)
            self.assertIn(b"\n", original.replace(b"\r\n", b""))

            normalize_rollout_sink(target)
            self.assertEqual(file_sha256(target), NORMALIZED_SHA256)
            self.assertNotIn(b"\r\n", target.read_bytes())
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "core.autocrlf", "false"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "apply", "--check", str(PATCH_0005)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "apply", str(PATCH_0005)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(file_sha256(target), POST_PATCH_SHA256)
            source = target.read_text(encoding="utf-8")

        replay = source.index("Reap.TreeSearch.MCTS.replaySolvedNode")
        proof_check = source.index("match ← Reap.TreeSearch.checkProof result.ctx", replay)
        selected = source.index("selectedEventIdsForSolvedNode result.nodes 0", proof_check)
        emitted = source.index('kind: "canonical_selected_path"', selected)
        solved_result = source.index(
            "writeJson resultPath <| resultJson sessionId (now - start) true", emitted
        )
        self.assertLess(replay, proof_check)
        self.assertLess(proof_check, selected)
        self.assertLess(selected, emitted)
        self.assertLess(emitted, solved_result)
        self.assertIn("if selectedEventIds.isEmpty then", source)
        self.assertIn('"selected_path_failed"', source)
        self.assertEqual(source.count('kind: "canonical_selected_path"'), 1)

    def test_patch_chain_defines_ids_before_emitting_selected_path(self) -> None:
        candidate_patch = PATCH_0004.read_text(encoding="utf-8")
        selected_patch = PATCH_0005.read_text(encoding="utf-8")
        prepare = PREPARE.read_text(encoding="utf-8")

        self.assertIn("selectedEventIdsForSolvedNode", candidate_patch)
        self.assertIn("canonicalEventId", candidate_patch)
        self.assertIn("canonical_selected_path", selected_patch)
        self.assertLess(
            prepare.index("0004-canonical-candidate-provenance.patch"),
            prepare.index("0005-canonical-selected-path-producer.patch"),
        )
        overlay_copy = prepare.index(
            'cp "$workstream_dir/runtime/overlay/Reap/Training/"*.lean'
        )
        normalize = prepare.index(
            'p.write_bytes(p.read_bytes().replace(b"\\r\\n", b"\\n"))'
        )
        patch_loop = prepare.index('for patch in "$workstream_dir/runtime/patches/')
        self.assertIn('"$reap_dir/Reap/Training/RolloutSink.lean"', prepare)
        self.assertLess(overlay_copy, normalize)
        self.assertLess(normalize, patch_loop)


if __name__ == "__main__":
    unittest.main()

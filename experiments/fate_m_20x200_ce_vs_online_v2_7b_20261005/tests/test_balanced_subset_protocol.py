from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = EXPERIMENT_ROOT / "data" / "build_balanced_subset.py"
CONFIG_PATH = EXPERIMENT_ROOT / "config" / "subset_20x10_protocol.frozen.json"

SPEC = importlib.util.spec_from_file_location("build_balanced_subset", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MATERIALIZE_PATH = EXPERIMENT_ROOT / "scripts" / "materialize_problems_jsonl.py"
MATERIALIZE_SPEC = importlib.util.spec_from_file_location(
    "materialize_problems_jsonl", MATERIALIZE_PATH
)
assert MATERIALIZE_SPEC is not None and MATERIALIZE_SPEC.loader is not None
MATERIALIZE = importlib.util.module_from_spec(MATERIALIZE_SPEC)
MATERIALIZE_SPEC.loader.exec_module(MATERIALIZE)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class BalancedSubsetProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config, cls.config_bytes = MODULE.load_protocol(CONFIG_PATH)

    def test_committed_artifacts_reproduce_byte_for_byte(self) -> None:
        expected = MODULE.build_artifacts(self.config, CONFIG_PATH, self.config_bytes)
        paths = MODULE.artifact_paths(self.config, CONFIG_PATH, output_dir=None)
        for name, payload in expected.items():
            with self.subTest(name=name):
                self.assertEqual(payload, paths[name].read_bytes())

    def test_manifest_is_balanced_disjoint_and_deterministically_ordered(self) -> None:
        manifest = load_jsonl(EXPERIMENT_ROOT / "data" / "subset_20x10_manifest.jsonl")
        train = [row for row in manifest if row["split"] == "train"]
        heldout = [row for row in manifest if row["split"] == "heldout"]

        self.assertEqual(200, len(manifest))
        self.assertEqual(160, len(train))
        self.assertEqual(40, len(heldout))
        self.assertEqual(set(range(1, 21)), {row["family_index"] for row in train})
        self.assertEqual(set(range(1, 21)), {row["family_index"] for row in heldout})
        self.assertEqual({8}, set(Counter(row["family_index"] for row in train).values()))
        self.assertEqual({2}, set(Counter(row["family_index"] for row in heldout).values()))
        self.assertEqual(set(range(1, 9)), {row["variant_index"] for row in train})
        self.assertEqual({9, 10}, {row["variant_index"] for row in heldout})
        self.assertFalse({row["id"] for row in train} & {row["id"] for row in heldout})
        self.assertEqual(
            [(row["variant_index"], row["family_index"]) for row in manifest],
            sorted((row["variant_index"], row["family_index"]) for row in manifest),
        )

    def test_full_records_match_manifest_and_source_hashes(self) -> None:
        manifest = load_jsonl(EXPERIMENT_ROOT / "data" / "subset_20x10_manifest.jsonl")
        train = load_jsonl(EXPERIMENT_ROOT / "data" / "subset_20x10_train.jsonl")
        heldout = load_jsonl(EXPERIMENT_ROOT / "data" / "subset_20x10_heldout.jsonl")
        by_split = {
            "train": train,
            "heldout": heldout,
        }
        for split, records in by_split.items():
            expected = [row for row in manifest if row["split"] == split]
            self.assertEqual([row["id"] for row in expected], [row["id"] for row in records])
            self.assertEqual(
                [row["record_sha256"] for row in expected],
                [row["sha256"] for row in records],
            )

    def test_wrong_source_hash_fails_closed(self) -> None:
        changed = json.loads(json.dumps(self.config))
        changed["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ProtocolError, "source sha256 mismatch"):
            MODULE.load_and_validate_source(changed, CONFIG_PATH)

    def test_compressed_source_materializes_losslessly_and_refuses_drift(self) -> None:
        import tempfile

        payload = b'{"id":"one"}\n{"id":"two"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compressed = root / "problems.jsonl.gz"
            compressed.write_bytes(gzip.compress(payload, mtime=0))
            output = root / "problems.jsonl"
            expected = hashlib.sha256(payload).hexdigest()
            resolved = MATERIALIZE.materialize(
                compressed, output, expected_sha256=expected, expected_rows=2
            )
            self.assertEqual(payload, resolved.read_bytes())
            output.write_bytes(b"drift\n")
            with self.assertRaisesRegex(FileExistsError, "noncanonical output"):
                MATERIALIZE.materialize(
                    compressed, output, expected_sha256=expected, expected_rows=2
                )

    def test_budget_seed_and_fairness_are_frozen(self) -> None:
        selection = self.config["selection"]
        budget = self.config["budget"]
        fairness = self.config["fairness"]
        self.assertEqual([1, 2, 3, 4, 5, 6, 7, 8], selection["train_variant_indices"])
        self.assertEqual([9, 10], selection["heldout_variant_indices"])
        self.assertEqual(20261005, self.config["randomness"]["base_seed"])
        self.assertEqual([4.0, 6.0], budget["expected_wall_hours_range"])
        self.assertEqual(4, budget["max_attempts_per_task"])
        self.assertEqual(512, budget["max_new_tokens_per_attempt"])
        self.assertTrue(fairness["same_problem_and_wave_order"])
        self.assertTrue(fairness["same_generation_seeds_by_paired_attempt"])
        self.assertTrue(fairness["same_primary_budget_caps"])
        self.assertTrue(fairness["heldout_is_never_used_for_selection"])


if __name__ == "__main__":
    unittest.main()

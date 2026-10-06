import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ce_arm.course import load_course, prepare_course, prepare_subset_course


def _make_course(path: Path):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for family in range(1, 21):
            for variant in range(1, 201):
                statement = f"theorem f{family}_v{variant} : True := by\n  sorry\n"
                row = {
                    "id": f"f{family}_v{variant}",
                    "family_index": family,
                    "variant_index": variant,
                    "formal_statement": statement,
                    "sha256": hashlib.sha256(statement.encode()).hexdigest(),
                }
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def test_prepare_course_has_leak_free_counts_and_waves(tmp_path):
    source = tmp_path / "problems.jsonl"
    _make_course(source)
    assert len(load_course(source)) == 4000
    manifest = prepare_course(source, tmp_path / "derived")
    assert manifest["adaptation_rows"] == 3500
    assert manifest["heldout_rows"] == 500
    assert manifest["files"]["waves.jsonl"]["rows"] == 175
    assert manifest["files"]["course_index.jsonl"]["rows"] == 4000
    rows = (tmp_path / "derived" / "waves" / "wave_175.jsonl").read_text().splitlines()
    assert len(rows) == 20
    assert all(json.loads(row)["id"].endswith("_v175") for row in rows)
    actor = json.loads(rows[0])
    assert actor["split"] == "adaptation"
    assert actor["variant_index"] == 175
    assert len(actor["statement_sha256"]) == 64


def test_course_rejects_statement_hash_mismatch(tmp_path):
    source = tmp_path / "bad.jsonl"
    _make_course(source)
    rows = source.read_text(encoding="utf-8").splitlines()
    item = json.loads(rows[0])
    item["formal_statement"] += "-- changed"
    rows[0] = json.dumps(item)
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    try:
        load_course(source)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("hash mismatch should be rejected")


def test_prepare_subset_course_freezes_160_40_and_eight_waves(tmp_path):
    source = tmp_path / "problems.jsonl"
    _make_course(source)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    train = [row for row in rows if row["variant_index"] <= 8]
    heldout = [row for row in rows if 9 <= row["variant_index"] <= 10]
    train_path = tmp_path / "train.jsonl"
    heldout_path = tmp_path / "heldout.jsonl"
    train_path.write_text("".join(json.dumps(row) + "\n" for row in train), encoding="utf-8")
    heldout_path.write_text("".join(json.dumps(row) + "\n" for row in heldout), encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({
        "status": "PASS", "protocol_id": "subset-test",
        "counts": {"selected": 200, "train": 160, "heldout": 40},
        "train_variant_indices": list(range(1, 9)),
        "heldout_variant_indices": [9, 10],
    }), encoding="utf-8")
    manifest = prepare_subset_course(train_path, heldout_path, selection, tmp_path / "subset")
    assert manifest["schema_version"] == 2
    assert manifest["adaptation_rows"] == 160
    assert manifest["heldout_rows"] == 40
    assert manifest["selected_rows"] == 200
    assert manifest["files"]["waves.jsonl"]["rows"] == 8
    index = [json.loads(line) for line in
             (tmp_path / "subset" / "course_index.jsonl").read_text().splitlines()]
    assert len(index) == 200
    assert sum(row["split"] == "adaptation" for row in index) == 160
    assert sum(row["split"] == "heldout" for row in index) == 40

"""Validate FATE-M 20x200 and derive leak-free actor inputs.

The source JSONL remains immutable.  This module produces deterministic files
for the 175 adaptation waves and the 25 held-out positions per family.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CourseProblem:
    problem_id: str
    family_index: int
    variant_index: int
    formal_statement: str
    source_sha256: str

    def actor_dict(self, split: str | None = None) -> dict:
        return {
            "id": self.problem_id,
            "family_index": self.family_index,
            "variant_index": self.variant_index,
            "split": split or ("adaptation" if self.variant_index <= 175 else "heldout"),
            "statement_sha256": self.source_sha256,
            "formal_statement": self.formal_statement,
        }

    def index_dict(self) -> dict:
        data = self.actor_dict()
        data.pop("formal_statement")
        return data


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl_write(path: Path, rows: Iterable[dict]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count, _sha256_bytes(path.read_bytes())


def _load_course_rows(path: str | Path) -> list[CourseProblem]:
    source = Path(path)
    result: list[CourseProblem] = []
    seen_ids: set[str] = set()
    seen_cells: set[tuple[int, int]] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON: {exc}") from exc
            required = ("id", "family_index", "variant_index", "formal_statement", "sha256")
            missing = [key for key in required if key not in item]
            if missing:
                raise ValueError(f"{source}:{line_number}: missing fields {missing}")
            problem = CourseProblem(
                problem_id=str(item["id"]),
                family_index=int(item["family_index"]),
                variant_index=int(item["variant_index"]),
                formal_statement=str(item["formal_statement"]),
                source_sha256=str(item["sha256"]).lower(),
            )
            if problem.problem_id in seen_ids:
                raise ValueError(f"duplicate problem id: {problem.problem_id}")
            cell = (problem.family_index, problem.variant_index)
            if cell in seen_cells:
                raise ValueError(f"duplicate family/variant cell: {cell}")
            if not 1 <= problem.family_index <= 20 or not 1 <= problem.variant_index <= 200:
                raise ValueError(f"out-of-range family/variant for {problem.problem_id}: {cell}")
            statement_hash = _sha256_bytes(problem.formal_statement.encode("utf-8"))
            if statement_hash != problem.source_sha256:
                raise ValueError(
                    f"statement hash mismatch for {problem.problem_id}: "
                    f"expected {problem.source_sha256}, got {statement_hash}"
                )
            seen_ids.add(problem.problem_id)
            seen_cells.add(cell)
            result.append(problem)
    return sorted(result, key=lambda p: (p.variant_index, p.family_index))


def load_course(path: str | Path) -> list[CourseProblem]:
    result = _load_course_rows(path)
    seen_cells = {(problem.family_index, problem.variant_index) for problem in result}
    expected = {(family, variant) for family in range(1, 21) for variant in range(1, 201)}
    missing_cells = expected - seen_cells
    if len(result) != 4000 or missing_cells:
        preview = sorted(missing_cells)[:8]
        raise ValueError(f"course must be a complete 20x200 grid; rows={len(result)}, missing={preview}")
    return sorted(result, key=lambda p: (p.variant_index, p.family_index))


def prepare_subset_course(train_source: str | Path, heldout_source: str | Path,
                          selection_receipt: str | Path, output_dir: str | Path) -> dict:
    """Publish the frozen 20-family v001-v010 course used by the short A/B run."""
    train_path = Path(train_source).resolve()
    heldout_path = Path(heldout_source).resolve()
    selection_path = Path(selection_receipt).resolve()
    output = Path(output_dir).resolve()
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("status") != "PASS" or selection.get("counts") != {
        "selected": 200, "train": 160, "heldout": 40,
    }:
        raise ValueError("subset selection receipt is not the frozen 160/40 PASS receipt")
    train = _load_course_rows(train_path)
    heldout = _load_course_rows(heldout_path)
    train_variants = set(selection.get("train_variant_indices", []))
    heldout_variants = set(selection.get("heldout_variant_indices", []))
    if len(train) != 160 or len(heldout) != 40:
        raise ValueError(f"subset rows must be train=160/heldout=40, got {len(train)}/{len(heldout)}")
    if {p.variant_index for p in train} != train_variants or train_variants != set(range(1, 9)):
        raise ValueError("subset train rows must be exactly variants 1..8")
    if {p.variant_index for p in heldout} != heldout_variants or heldout_variants != {9, 10}:
        raise ValueError("subset heldout rows must be exactly variants 9..10")
    train_cells = {(p.family_index, p.variant_index) for p in train}
    heldout_cells = {(p.family_index, p.variant_index) for p in heldout}
    expected_train = {(family, variant) for variant in range(1, 9) for family in range(1, 21)}
    expected_heldout = {(family, variant) for variant in range(9, 11) for family in range(1, 21)}
    if train_cells != expected_train or heldout_cells != expected_heldout:
        raise ValueError("subset does not contain the complete balanced 20x(8+2) grid")
    if {p.problem_id for p in train} & {p.problem_id for p in heldout}:
        raise ValueError("subset train and heldout ids overlap")

    files: dict[str, dict] = {}
    count, digest = _jsonl_write(
        output / "adaptation.jsonl", (p.actor_dict("adaptation") for p in train)
    )
    files["adaptation.jsonl"] = {"rows": count, "sha256": digest}
    count, digest = _jsonl_write(
        output / "heldout.jsonl", (p.actor_dict("heldout") for p in heldout)
    )
    files["heldout.jsonl"] = {"rows": count, "sha256": digest}
    all_rows = sorted(train + heldout, key=lambda p: (p.variant_index, p.family_index))
    count, digest = _jsonl_write(
        output / "course_index.jsonl",
        (p.actor_dict("adaptation" if p.variant_index <= 8 else "heldout") for p in all_rows),
    )
    files["course_index.jsonl"] = {"rows": count, "sha256": digest}
    waves = []
    for variant in range(1, 9):
        wave = [p for p in train if p.variant_index == variant]
        relative = Path("waves") / f"wave_{variant:03d}.jsonl"
        count, digest = _jsonl_write(
            output / relative, (p.actor_dict("adaptation") for p in wave)
        )
        waves.append({"wave_index": variant, "variant_index": variant,
                      "path": relative.as_posix(), "rows": count, "sha256": digest,
                      "problem_ids": [p.problem_id for p in wave]})
    count, digest = _jsonl_write(output / "waves.jsonl", waves)
    files["waves.jsonl"] = {"rows": count, "sha256": digest}
    manifest = {
        "schema_version": 2,
        "protocol_id": selection["protocol_id"],
        "selection_receipt_sha256": _sha256_bytes(selection_path.read_bytes()),
        "train_source_sha256": _sha256_bytes(train_path.read_bytes()),
        "heldout_source_sha256": _sha256_bytes(heldout_path.read_bytes()),
        "families": 20, "selected_variants_per_family": 10,
        "adaptation_range": [1, 8], "heldout_range": [9, 10],
        "adaptation_rows": 160, "heldout_rows": 40, "selected_rows": 200,
        "files": files,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def prepare_course(source: str | Path, output_dir: str | Path) -> dict:
    source = Path(source).resolve()
    output = Path(output_dir).resolve()
    problems = load_course(source)
    adaptation = [p for p in problems if p.variant_index <= 175]
    heldout = [p for p in problems if p.variant_index >= 176]

    files: dict[str, dict] = {}
    count, digest = _jsonl_write(output / "adaptation.jsonl", (p.actor_dict() for p in adaptation))
    files["adaptation.jsonl"] = {"rows": count, "sha256": digest}
    count, digest = _jsonl_write(output / "heldout.jsonl", (p.actor_dict() for p in heldout))
    files["heldout.jsonl"] = {"rows": count, "sha256": digest}
    count, digest = _jsonl_write(output / "course_index.jsonl", (p.index_dict() for p in problems))
    files["course_index.jsonl"] = {"rows": count, "sha256": digest}

    wave_rows = []
    for variant in range(1, 176):
        wave = [p for p in adaptation if p.variant_index == variant]
        relative = Path("waves") / f"wave_{variant:03d}.jsonl"
        count, digest = _jsonl_write(output / relative, (p.actor_dict() for p in wave))
        if count != 20:
            raise AssertionError(f"wave {variant} has {count} problems, expected 20")
        wave_rows.append(
            {
                "wave_index": variant,
                "variant_index": variant,
                "path": relative.as_posix(),
                "rows": count,
                "sha256": digest,
                "problem_ids": [p.problem_id for p in wave],
            }
        )
    count, digest = _jsonl_write(output / "waves.jsonl", wave_rows)
    files["waves.jsonl"] = {"rows": count, "sha256": digest}

    manifest = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": _sha256_bytes(source.read_bytes()),
        "families": 20,
        "variants_per_family": 200,
        "adaptation_range": [1, 175],
        "heldout_range": [176, 200],
        "adaptation_rows": len(adaptation),
        "heldout_rows": len(heldout),
        "files": files,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest

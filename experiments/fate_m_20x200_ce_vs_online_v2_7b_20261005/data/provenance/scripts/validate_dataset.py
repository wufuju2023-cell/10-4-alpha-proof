#!/usr/bin/env python3
"""Perform deterministic structural and content checks on the generated delivery."""

from __future__ import annotations

import collections
import csv
import hashlib
import json
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"VALIDATION FAILED: {message}")


def main() -> None:
    config = json.loads((ROOT / "config" / "selection.json").read_text(encoding="utf-8"))
    jsonl_path = ROOT / "data" / "problems.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line]
    expected_total = config["total_variants"]
    if len(rows) != expected_total:
        fail(f"expected {expected_total} JSONL rows, found {len(rows)}")

    ids = [row["id"] for row in rows]
    if len(set(ids)) != expected_total:
        fail("problem ids are not unique")
    hashes = [row["sha256"] for row in rows]
    if len(set(hashes)) != expected_total:
        fail("standalone formal statements are not byte-unique")

    family_counts = collections.Counter(int(row["fate_id"]) for row in rows)
    expected_family_counts = {problem_id: config["variants_per_family"] for problem_id in config["selected_fate_ids"]}
    if dict(family_counts) != expected_family_counts:
        fail(f"family counts differ: {dict(family_counts)}")

    for row in rows:
        code = row["formal_statement"]
        actual_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if actual_hash != row["sha256"]:
            fail(f"hash mismatch for {row['id']}")
        if code.count("import Mathlib") != 1:
            fail(f"{row['id']} does not import Mathlib exactly once")
        if len(re.findall(r"\bsorry\b", code)) != 1:
            fail(f"{row['id']} must contain exactly one proof hole")
        if f"theorem {row['theorem_name']}" not in code:
            fail(f"theorem name mismatch for {row['id']}")
        family_dir = ROOT / "lean" / "problems" / f"P{row['family_index']:02d}_FATE_M_{row['fate_id']:03d}"
        lean_path = family_dir / f"v{row['variant_index']:03d}.lean"
        if not lean_path.is_file():
            fail(f"missing standalone file {lean_path}")
        if lean_path.read_text(encoding="utf-8") != code:
            fail(f"standalone file differs from JSONL for {row['id']}")

    manifest_path = ROOT / "data" / "manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        manifest_rows = list(csv.DictReader(handle))
    if len(manifest_rows) != expected_total:
        fail(f"expected {expected_total} CSV rows, found {len(manifest_rows)}")

    family_files = sorted((ROOT / "lean" / "families").glob("*.lean"))
    if len(family_files) != len(config["selected_fate_ids"]):
        fail(f"expected 20 family Lean files, found {len(family_files)}")
    for family_file in family_files:
        text = family_file.read_text(encoding="utf-8")
        if len(re.findall(r"(?m)^theorem fate_m_", text)) != config["variants_per_family"]:
            fail(f"{family_file.name} does not contain 200 theorem declarations")
        if len(re.findall(r"\bsorry\b", text)) != config["variants_per_family"]:
            fail(f"{family_file.name} does not contain 200 proof holes")

    print("VALIDATION PASSED")
    print(f"families={len(family_files)} variants={len(rows)} unique_ids={len(set(ids))} unique_code={len(set(hashes))}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create the Reap prompt-capture plan for the frozen 40-row held-out set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


FULL_PROBLEMS_SHA256 = "3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d"
HELDOUT_SHA256 = "a394465ef3e74666abea400496672ee97e838ddf4364c390d85362fb5d497c09"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_plan(problems: Path, heldout: Path) -> dict:
    if sha256_file(problems) != FULL_PROBLEMS_SHA256:
        raise ValueError("authoritative 4000-row problems.jsonl hash mismatch")
    if sha256_file(heldout) != HELDOUT_SHA256:
        raise ValueError("frozen held-out JSONL hash mismatch")
    all_rows = load_jsonl(problems)
    heldout_rows = load_jsonl(heldout)
    if len(all_rows) != 4000 or len(heldout_rows) != 40:
        raise ValueError("expected 4000 source rows and 40 held-out rows")
    expected_pairs = {(variant, family) for variant in (9, 10) for family in range(1, 21)}
    actual_pairs = {(int(row["variant_index"]), int(row["family_index"])) for row in heldout_rows}
    if actual_pairs != expected_pairs:
        raise ValueError("held-out rows must cover v009-v010 for all 20 families exactly once")
    by_id = {row["id"]: row for row in all_rows}
    for row in heldout_rows:
        source = by_id.get(row["id"])
        if source != row:
            raise ValueError(f"held-out row differs from authoritative source: {row['id']}")
    tasks = [
        {
            "session_id": row["id"],
            "family_index": int(row["family_index"]),
            "variant_index": int(row["variant_index"]),
            "formal_statement_sha256": row["sha256"],
        }
        for row in heldout_rows
    ]
    return {
        "schema_version": "fate.policy_service.representative_smoke_plan.v1",
        "status": "frozen_heldout_prompt_capture_plan",
        "source": {
            "problems_relative_path": "../../../data/problems.jsonl",
            "problems_sha256": FULL_PROBLEMS_SHA256,
            "required_record_count": 4000,
        },
        "expected_session_count": 40,
        "tasks": tasks,
        "protocol": {
            "selection": "frozen v009-v010 across all 20 families",
            "heldout_sha256": HELDOUT_SHA256,
            "prompt_owner": "pinned Reap TacticGenerator.mkPrompt at point of use",
            "training": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--heldout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists")
    plan = build_plan(args.problems.resolve(), args.heldout.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(args.output.resolve()), "tasks": len(plan["tasks"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

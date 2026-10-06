#!/usr/bin/env python3
"""Freeze one 20-family formal rollout plan from the authoritative problems JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_plan(problems: Path, wave_index: int) -> dict:
    if not 1 <= wave_index <= 200:
        raise ValueError("wave-index must be in 1..200")
    rows = []
    seen = set()
    for line_number, line in enumerate(problems.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        session_id = row.get("id")
        if not isinstance(session_id, str) or session_id in seen:
            raise ValueError(f"invalid/duplicate problem id at line {line_number}")
        statement = str(row.get("formal_statement", ""))
        if hashlib.sha256(statement.encode("utf-8")).hexdigest() != row.get("sha256"):
            raise ValueError(f"formal statement hash mismatch: {session_id}")
        seen.add(session_id)
        if row.get("variant_index") == wave_index:
            rows.append(row)
    rows.sort(key=lambda row: int(row["family_index"]))
    if len(rows) != 20 or [int(row["family_index"]) for row in rows] != list(range(1, 21)):
        raise ValueError("wave must select exactly families 1..20")
    return {
        "schema_version": "fate.policy_service.representative_smoke_plan.v1",
        "status": "frozen_formal_wave",
        "expected_session_count": 20,
        "source": {
            "problems_relative_path": "../../../data/problems.jsonl",
            "problems_sha256": sha256_file(problems),
            "required_record_count": len(seen),
        },
        "tasks": [{
            "session_id": row["id"],
            "family_index": row["family_index"],
            "variant_index": row["variant_index"],
            "formal_statement_sha256": row["sha256"],
        } for row in rows],
        "protocol": {
            "generation": {
                "temperature": 1.5, "top_p": 0.9, "max_tokens": 512,
                "n": 64, "logprobs": True, "rescore_micro_batch": 1,
            },
            "search": {"max_steps": 1, "max_goals": 64, "num_premises": 0},
            "max_attempts_per_problem": 4,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists")
    value = build_plan(args.problems, args.wave_index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".pending")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                indent=2).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.output)
    print(json.dumps({"event": "formal_wave_plan_ready", "wave_index": args.wave_index,
                      "plan": str(args.output.resolve()),
                      "sha256": sha256_file(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

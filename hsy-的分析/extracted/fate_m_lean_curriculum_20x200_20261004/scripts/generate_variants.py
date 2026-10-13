#!/usr/bin/env python3
"""Generate 20 x 200 ordered Lean curriculum variants from pinned FATE-M statements."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re
import shutil
from dataclasses import dataclass


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "selection.json"
ORIGINALS_PATH = ROOT / "data" / "originals.json"
JSONL_PATH = ROOT / "data" / "problems.jsonl"
MANIFEST_PATH = ROOT / "data" / "manifest.csv"
FAMILY_ROOT = ROOT / "lean" / "families"
PROBLEM_ROOT = ROOT / "lean" / "problems"


@dataclass(frozen=True)
class ParsedTheorem:
    original_name: str
    binders: str
    goal: str
    opens: tuple[str, ...]


def parse_theorem(source: str) -> ParsedTheorem:
    theorem_match = re.search(r"(?m)^theorem\s+([A-Za-z0-9_'.]+)", source)
    if theorem_match is None:
        raise ValueError("formal_statement contains no theorem declaration")

    name = theorem_match.group(1)
    binder_start = theorem_match.end()
    depth = 0
    colon_index = None
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    for index in range(binder_start, len(source)):
        char = source[index]
        if char in pairs:
            depth += 1
        elif char in closing:
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced delimiter in theorem {name}")
        elif char == ":" and depth == 0:
            colon_index = index
            break
    if colon_index is None:
        raise ValueError(f"could not find result colon for theorem {name}")

    proof_match = re.search(r"\s*:=\s*by\s+sorry\s*$", source[colon_index + 1 :], re.S)
    if proof_match is None:
        raise ValueError(f"theorem {name} does not end in the expected `by sorry`")
    goal_end = colon_index + 1 + proof_match.start()

    binders = source[binder_start:colon_index].strip()
    goal = source[colon_index + 1 : goal_end].strip()
    opens = tuple(
        line.strip()
        for line in source[: theorem_match.start()].splitlines()
        if line.strip().startswith("open ")
    )
    return ParsedTheorem(name, binders, goal, opens)


def atom(number: int) -> str:
    """A closed, definitionally true, rank-specific proposition."""
    return f"(({number} : Nat) = {number})"


def conjunction(parts: list[str]) -> str:
    assert parts
    expression = parts[-1]
    for part in reversed(parts[:-1]):
        expression = f"({part} ∧ {expression})"
    return expression


def implication(parts: list[str], target: str) -> str:
    expression = target
    for part in reversed(parts):
        expression = f"({part} → {expression})"
    return expression


def disjunction(parts: list[str]) -> str:
    assert parts
    expression = parts[-1]
    for part in reversed(parts[:-1]):
        expression = f"({part} ∨ {expression})"
    return expression


def tier_for(rank: int) -> tuple[int, str, int]:
    if 1 <= rank <= 40:
        return 1, "direct_target", rank
    if rank <= 80:
        return 2, "packed_target", rank - 40
    if rank <= 120:
        return 3, "implication_chain", rank - 80
    if rank <= 160:
        return 4, "disjunction_elimination", rank - 120
    if rank <= 175:
        return 5, "iff_bridge", rank - 160
    if rank <= 200:
        return 6, "unscaffolded", rank - 176
    raise ValueError(f"variant rank out of range: {rank}")


def make_variant(parsed: ParsedTheorem, family_index: int, fate_id: int, rank: int) -> dict:
    tier, transformation, depth = tier_for(rank)
    theorem_name = f"fate_m_{fate_id:03d}_v{rank:03d}"
    base_goal = f"({parsed.goal})"
    extras: list[str] = []
    final_goal = base_goal

    if tier == 1:
        extras.append(f"(curriculum_target : {base_goal})")
        for index in range(1, depth):
            extras.append(
                f"(curriculum_context_{index:03d} : {atom(100000 + rank * 100 + index)})"
            )
    elif tier == 2:
        facts = [atom(200000 + rank * 100 + index) for index in range(1, depth + 1)]
        extras.append(f"(curriculum_pack : {conjunction(facts + [base_goal])})")
    elif tier == 3:
        facts = [atom(300000 + rank * 100 + index) for index in range(1, depth + 1)]
        extras.append(f"(curriculum_step : {implication(facts, base_goal)})")
        for index, fact in enumerate(facts, start=1):
            extras.append(f"(curriculum_fact_{index:03d} : {fact})")
    elif tier == 4:
        impossible = [
            conjunction([atom(400000 + rank * 100 + index), "False"])
            for index in range(1, depth + 1)
        ]
        extras.append(f"(curriculum_cases : {disjunction([base_goal] + impossible)})")
    elif tier == 5:
        bridge_atoms = [atom(500000 + rank * 100 + index) for index in range(1, depth + 1)]
        previous = base_goal
        for index, next_atom in enumerate(bridge_atoms, start=1):
            extras.append(f"(curriculum_bridge_{index:03d} : ({previous} ↔ {next_atom}))")
            previous = next_atom
        extras.append(f"(curriculum_fact : {bridge_atoms[-1]})")
    else:
        if depth:
            obligations = [atom(600000 + rank * 100 + index) for index in range(1, depth + 1)]
            final_goal = conjunction([base_goal] + obligations)

    binder_parts = []
    if parsed.binders:
        binder_parts.append(parsed.binders)
    binder_parts.extend(extras)
    rendered_binders = "\n    ".join(binder_parts)
    declaration = (
        f"theorem {theorem_name} {rendered_binders} :\n"
        f"    {final_goal} := by\n"
        "  sorry"
    )
    header_lines = ["import Mathlib", ""]
    if parsed.opens:
        header_lines.extend(parsed.opens)
        header_lines.append("")
    header_lines.extend(
        [
            "namespace FateCurriculum",
            "",
            f"/-- FATE-M #{fate_id}, curriculum variant {rank}/200; tier {tier}: {transformation}. -/",
            declaration,
            "",
            "end FateCurriculum",
            "",
        ]
    )
    formal_statement = "\n".join(header_lines)
    return {
        "id": theorem_name,
        "family_index": family_index,
        "fate_id": fate_id,
        "variant_index": rank,
        "difficulty_rank": rank,
        "difficulty_tier": tier,
        "transformation": transformation,
        "transformation_depth": depth,
        "theorem_name": theorem_name,
        "declaration": declaration,
        "formal_statement": formal_statement,
        "sha256": hashlib.sha256(formal_statement.encode("utf-8")).hexdigest(),
    }


def write_family_file(
    family_index: int,
    fate_id: int,
    parsed: ParsedTheorem,
    variants: list[dict],
) -> pathlib.Path:
    lines = ["import Mathlib", ""]
    if parsed.opens:
        lines.extend(parsed.opens)
        lines.append("")
    lines.extend(
        [
            "namespace FateCurriculum",
            "",
            f"/-! Generated curriculum family P{family_index:02d}, derived from FATE-M #{fate_id}. -/",
            "",
        ]
    )
    for variant in variants:
        lines.extend(
            [
                f"/-- Variant {variant['variant_index']}/200; tier {variant['difficulty_tier']}: {variant['transformation']}. -/",
                variant["declaration"],
                "",
            ]
        )
    lines.extend(["end FateCurriculum", ""])
    path = FAMILY_ROOT / f"P{family_index:02d}_FATE_M_{fate_id:03d}.lean"
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    originals = json.loads(ORIGINALS_PATH.read_text(encoding="utf-8"))
    problems = originals["problems"]
    expected_ids = config["selected_fate_ids"]
    actual_ids = [int(problem["id"]) for problem in problems]
    if actual_ids != expected_ids:
        raise SystemExit(f"original order mismatch: expected {expected_ids}, got {actual_ids}")

    if FAMILY_ROOT.exists():
        shutil.rmtree(FAMILY_ROOT)
    if PROBLEM_ROOT.exists():
        shutil.rmtree(PROBLEM_ROOT)
    FAMILY_ROOT.mkdir(parents=True)
    PROBLEM_ROOT.mkdir(parents=True)

    rows: list[dict] = []
    for family_index, problem in enumerate(problems, start=1):
        fate_id = int(problem["id"])
        parsed = parse_theorem(problem["formal_statement"])
        family_variants = []
        family_dir = PROBLEM_ROOT / f"P{family_index:02d}_FATE_M_{fate_id:03d}"
        family_dir.mkdir(parents=True)
        for rank in range(1, config["variants_per_family"] + 1):
            variant = make_variant(parsed, family_index, fate_id, rank)
            variant["source"] = "FATE-M"
            variant["source_commit"] = config["source"]["submodule_commit"]
            variant["source_theorem_name"] = parsed.original_name
            variant["informal_statement"] = problem["informal_statement"]
            family_variants.append(variant)
            rows.append(variant)
            (family_dir / f"v{rank:03d}.lean").write_text(
                variant["formal_statement"], encoding="utf-8", newline="\n"
            )
        write_family_file(family_index, fate_id, parsed, family_variants)

    with JSONL_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            serializable = {key: value for key, value in row.items() if key != "declaration"}
            handle.write(json.dumps(serializable, ensure_ascii=False, separators=(",", ":")) + "\n")

    manifest_fields = [
        "id",
        "family_index",
        "fate_id",
        "variant_index",
        "difficulty_rank",
        "difficulty_tier",
        "transformation",
        "transformation_depth",
        "theorem_name",
        "source",
        "source_commit",
        "source_theorem_name",
        "sha256",
    ]
    with MANIFEST_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"generated {len(rows)} variants across {len(problems)} families")
    print(f"standalone Lean: {PROBLEM_ROOT}")
    print(f"batched Lean:    {FAMILY_ROOT}")
    print(f"JSONL:           {JSONL_PATH}")


if __name__ == "__main__":
    main()

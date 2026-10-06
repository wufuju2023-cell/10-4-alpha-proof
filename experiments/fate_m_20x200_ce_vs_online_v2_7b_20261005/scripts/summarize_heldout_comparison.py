#!/usr/bin/env python3
"""Validate and summarize initial/CE-final/Online-final held-out reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


LABELS = ("initial", "ce_final", "online_final")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    os.replace(temporary, path)


def load_report(root: Path, expected_label: str) -> dict[str, Any]:
    done = load_json(root / "DONE.json")
    report_path = root / "report.json"
    if done.get("state") != "DONE" or done.get("checkpoint_label") != expected_label:
        raise ValueError(f"{expected_label} evaluation is not DONE")
    if done.get("report_sha256") != sha256_file(report_path):
        raise ValueError(f"{expected_label} report hash mismatch")
    report = load_json(report_path)
    if report.get("checkpoint_label") != expected_label or report.get("state") != "DONE":
        raise ValueError(f"{expected_label} report identity mismatch")
    if report.get("metrics", {}).get("tasks") != 40:
        raise ValueError(f"{expected_label} does not contain all 40 held-out tasks")
    return report


def task_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = {str(row["session_id"]): row for row in report["task_results"]}
    if len(tasks) != 40:
        raise ValueError("task IDs are missing or duplicated")
    return tasks


def paired_counts(left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> dict[str, int]:
    if set(left) != set(right):
        raise ValueError("paired task sets differ")
    left_only = sum(bool(left[key]["solved"]) and not bool(right[key]["solved"]) for key in left)
    right_only = sum(bool(right[key]["solved"]) and not bool(left[key]["solved"]) for key in left)
    both = sum(bool(left[key]["solved"]) and bool(right[key]["solved"]) for key in left)
    neither = len(left) - left_only - right_only - both
    return {"left_only": left_only, "right_only": right_only, "both": both, "neither": neither}


def summarize(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fingerprints = {report["settings_fingerprint"] for report in reports.values()}
    if len(fingerprints) != 1:
        raise ValueError("checkpoint evaluations do not share identical frozen settings")
    tasks = {label: task_map(report) for label, report in reports.items()}
    if len({tuple(mapping) for mapping in tasks.values()}) != 1:
        raise ValueError("checkpoint evaluations use different held-out order")
    metrics = {label: report["metrics"] for label, report in reports.items()}
    initial_rate = float(metrics["initial"]["solve_rate"])
    ce_rate = float(metrics["ce_final"]["solve_rate"])
    online_rate = float(metrics["online_final"]["solve_rate"])
    paired_ce_online = paired_counts(tasks["ce_final"], tasks["online_final"])
    family_differences = {}
    for family in range(1, 21):
        ce_family = float(metrics["ce_final"]["by_family"][str(family)]["solve_rate"])
        online_family = float(metrics["online_final"]["by_family"][str(family)]["solve_rate"])
        family_differences[str(family)] = ce_family - online_family
    if ce_rate > online_rate:
        descriptive_winner = "ce_final"
    elif online_rate > ce_rate:
        descriptive_winner = "online_final"
    else:
        descriptive_winner = "tie"
    return {
        "schema_version": "fate.heldout_three_checkpoint_comparison.v1",
        "state": "DONE",
        "settings_fingerprint": next(iter(fingerprints)),
        "checkpoints": {
            label: {
                "solved_count": metrics[label]["solved_count"],
                "tasks": metrics[label]["tasks"],
                "solve_rate": metrics[label]["solve_rate"],
                "pass_at_1": metrics[label]["pass_at_1"],
                "pass_at_2": metrics[label]["pass_at_2"],
                "pass_at_4": metrics[label]["pass_at_4"],
                "started_attempts": metrics[label]["started_attempts"],
                "generated_tokens": metrics[label]["generated_tokens"],
                "lean_tactic_executions": metrics[label]["lean_tactic_executions"],
                "wall_seconds": reports[label]["wall_seconds"],
                "truncated_attempts": metrics[label]["truncated_attempts"],
                "truncation_rate": metrics[label]["truncation_rate"],
                "timeout_attempts": metrics[label]["timeout_attempts"],
            }
            for label in LABELS
        },
        "effects": {
            "ce_minus_initial_solved": int(metrics["ce_final"]["solved_count"])
            - int(metrics["initial"]["solved_count"]),
            "online_minus_initial_solved": int(metrics["online_final"]["solved_count"])
            - int(metrics["initial"]["solved_count"]),
            "ce_minus_online_solved": int(metrics["ce_final"]["solved_count"])
            - int(metrics["online_final"]["solved_count"]),
            "ce_minus_initial_rate": ce_rate - initial_rate,
            "online_minus_initial_rate": online_rate - initial_rate,
            "ce_minus_online_rate": ce_rate - online_rate,
            "paired_ce_vs_online": paired_ce_online,
            "per_family_ce_minus_online_rate": family_differences,
        },
        "conclusion": {
            "descriptive_winner": descriptive_winner,
            "claim_limit": (
                "Single-seed scaffolded within-family v009-v010 transfer only; "
                "this is descriptive, not a significance or robustness claim. "
                "Training exposure and original protocol completion require separate run evidence."
            ),
        },
    }


def markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Frozen held-out comparison",
        "",
        "| checkpoint | solved / 40 | pass@1 | pass@2 | pass@4 | truncation | wall min |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label in LABELS:
        row = comparison["checkpoints"][label]
        lines.append(
            f"| {label} | {row['solved_count']} / 40 | {row['pass_at_1']:.3f} | "
            f"{row['pass_at_2']:.3f} | {row['pass_at_4']:.3f} | "
            f"{row['truncation_rate']:.3f} | {row['wall_seconds'] / 60:.2f} |"
        )
    effects = comparison["effects"]
    lines += [
        "",
        f"Descriptive result: **{comparison['conclusion']['descriptive_winner']}**. "
        f"CE−initial = {effects['ce_minus_initial_solved']:+d} solves; "
        f"Online−initial = {effects['online_minus_initial_solved']:+d}; "
        f"CE−Online = {effects['ce_minus_online_solved']:+d}.",
        "",
        comparison["conclusion"]["claim_limit"],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--ce-final", type=Path, required=True)
    parser.add_argument("--online-final", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    reports = {
        "initial": load_report(args.initial, "initial"),
        "ce_final": load_report(args.ce_final, "ce_final"),
        "online_final": load_report(args.online_final, "online_final"),
    }
    comparison = summarize(reports)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = args.output_dir / "comparison.json"
    atomic_json(comparison_path, comparison)
    markdown_path = args.output_dir / "comparison.md"
    markdown_path.write_text(markdown(comparison), encoding="utf-8", newline="\n")
    done = {
        "state": "DONE",
        "comparison_sha256": sha256_file(comparison_path),
        "markdown_sha256": sha256_file(markdown_path),
    }
    atomic_json(args.output_dir / "DONE.json", done)
    print(json.dumps(comparison, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

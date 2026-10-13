#!/usr/bin/env python3
"""Compile all 20 batched Lean families and save a machine-readable report."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
FAMILY_ROOT = ROOT / "lean" / "families"
OUTPUT_ROOT = ROOT / "build" / "lean-validation"


def find_lake() -> str:
    configured = os.environ.get("FATE_LAKE")
    if configured:
        return configured
    discovered = shutil.which("lake")
    if discovered:
        return discovered
    windows_fallback = pathlib.Path.home() / ".elan" / "bin" / "lake.exe"
    if windows_fallback.is_file():
        return str(windows_fallback)
    raise SystemExit("lake was not found; set FATE_LAKE to the executable path")


def compile_one(lake: str, path: pathlib.Path) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        [lake, "env", "lean", str(path.relative_to(ROOT))],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed = time.monotonic() - started
    log_path = OUTPUT_ROOT / f"{path.stem}.log"
    log_path.write_text(completed.stdout, encoding="utf-8", newline="\n")
    return {
        "file": path.relative_to(ROOT).as_posix(),
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "log": log_path.relative_to(ROOT).as_posix(),
    }


def main() -> None:
    lake = find_lake()
    files = sorted(FAMILY_ROOT.glob("*.lean"))
    if len(files) != 20:
        raise SystemExit(f"expected 20 family files, found {len(files)}")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for old_log in OUTPUT_ROOT.glob("*.log"):
        old_log.unlink()

    started = time.monotonic()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(compile_one, lake, path): path for path in files}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            print(
                f"[{completed_count:02d}/{len(files)}] {result['file']} "
                f"exit={result['exit_code']} elapsed={result['elapsed_seconds']}s",
                flush=True,
            )

    results.sort(key=lambda item: item["file"])
    report = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "command": "lake env lean <family-file>",
        "parallel_workers": 4,
        "family_files": len(files),
        "theorems": len(files) * 200,
        "passed": sum(result["exit_code"] == 0 for result in results),
        "failed": sum(result["exit_code"] != 0 for result in results),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }
    report_path = OUTPUT_ROOT / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report: {report_path}")
    if report["failed"]:
        raise SystemExit(f"Lean validation failed for {report['failed']} family file(s)")


if __name__ == "__main__":
    main()

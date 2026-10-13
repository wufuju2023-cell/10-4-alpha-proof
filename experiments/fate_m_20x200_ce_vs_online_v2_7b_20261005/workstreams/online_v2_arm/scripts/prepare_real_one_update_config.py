#!/usr/bin/env python3
"""Freeze exact pins for a one-problem smoke or 20-problem formal Online-v2 update."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alphaproof_online_v2_arm.real_runner import (  # noqa: E402
    RealRunnerError,
    prepare_update_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joined", type=Path, action="append", required=True)
    parser.add_argument("--signed-receipt", type=Path, action="append", required=True)
    parser.add_argument("--mode", choices=("smoke", "formal"), default="smoke")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--value-head", type=Path, required=True)
    parser.add_argument("--target-repo", type=Path, required=True)
    parser.add_argument("--behavior-adapter-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        path, digest = prepare_update_config(
            joined=args.joined, signed_receipt=args.signed_receipt, mode=args.mode,
            model=args.model,
            adapter=args.adapter, value_head=args.value_head,
            target_repo=args.target_repo,
            behavior_adapter_name=args.behavior_adapter_name, output=args.output,
        )
    except RealRunnerError as exc:
        parser.exit(2, f"fail closed: {exc}\n")
    print(json.dumps({"config": str(path), "sha256": digest}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Assemble one 20-problem wave of join outputs for both learners."""

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


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def assemble(*, plan_path: Path, join_root: Path, output_dir: Path,
             wave_index: int) -> dict[str, object]:
    """Validate and exclusively publish the two learner input sets."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if len(plan.get("tasks", [])) != 20:
        raise ValueError("wave assembly requires exactly 20 planned tasks")
    if isinstance(wave_index, bool) or not 1 <= wave_index <= 200:
        raise ValueError("wave-index must be an integer in 1..200")
    sessions = [task.get("session_id") for task in plan["tasks"]]
    if any(not isinstance(value, str) or not value for value in sessions):
        raise ValueError("wave plan has a missing session_id")
    if len(set(sessions)) != 20:
        raise ValueError("wave plan session_ids are not unique")
    expected_suffix = f"_v{wave_index:03d}"
    if any(not value.endswith(expected_suffix) for value in sessions):
        raise ValueError("wave plan session_id does not match --wave-index")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True)
    signed_rows, ce_rows, online_pins, behavior = [], [], [], set()
    for task in plan["tasks"]:
        session_id = task["session_id"]
        root = join_root / session_id
        signed_path = root / "strict-replay" / "receipt.json"
        ce_path = root / "ce-receipt.json"
        online_path = root / "online-v2-receipts.json"
        for path in (signed_path, ce_path, online_path):
            if not path.is_file():
                raise FileNotFoundError(path)
        signed = json.loads(signed_path.read_text(encoding="utf-8"))
        ce = json.loads(ce_path.read_text(encoding="utf-8"))
        online = json.loads(online_path.read_text(encoding="utf-8"))
        if signed.get("problem_id") != session_id or ce.get("problem_id") != session_id:
            raise ValueError(f"{session_id}: joined receipt problem identity mismatch")
        if signed.get("request", {}).get("wave_index") != wave_index:
            raise ValueError(f"{session_id}: joined receipt wave identity mismatch")
        if ce != signed:
            raise ValueError(f"{session_id}: CE converter did not preserve the signed receipt")
        if online.get("source_receipt_sha256") != signed.get("receipt_sha256"):
            raise ValueError(f"{session_id}: online join source mismatch")
        behavior.add(canonical(signed.get("behavior_identity")))
        signed_rows.append(signed); ce_rows.append(ce)
        online_pins.append({
            "path": str(online_path.resolve()), "sha256": sha256_file(online_path),
            "source_receipt_sha256": signed["receipt_sha256"],
            "source_receipt_path": str(signed_path.resolve()),
            "source_receipt_file_sha256": sha256_file(signed_path),
        })
    if len(behavior) != 1:
        raise ValueError("wave contains mixed behavior identities")
    publish(output_dir / "signed-receipts.jsonl",
            b"".join(canonical(row) + b"\n" for row in signed_rows))
    publish(output_dir / "ce-receipts.jsonl",
            b"".join(canonical(row) + b"\n" for row in ce_rows))
    pins_path = output_dir / "online-v2-receipt-pins.json"
    publish(pins_path, json.dumps(online_pins, ensure_ascii=False, sort_keys=True,
                                  indent=2).encode("utf-8") + b"\n")
    manifest = {
        "schema_version": "fate.wave.join_inputs.v1", "status": "complete",
        "wave_index": wave_index,
        "plan": {"path": str(plan_path.resolve()), "sha256": sha256_file(plan_path)},
        "sessions": sessions,
        "signed_receipts": {"path": str((output_dir / "signed-receipts.jsonl").resolve()),
                            "sha256": sha256_file(output_dir / "signed-receipts.jsonl")},
        "ce_receipts": {"path": str((output_dir / "ce-receipts.jsonl").resolve()),
                        "sha256": sha256_file(output_dir / "ce-receipts.jsonl")},
        "online_receipt_pins_file": {"path": str(pins_path.resolve()),
                                     "sha256": sha256_file(pins_path)},
        "online_receipt_pins": online_pins,
    }
    publish(output_dir / "manifest.json", json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--join-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wave-index", type=int, required=True)
    args = parser.parse_args()
    assemble(plan_path=args.plan, join_root=args.join_root,
             output_dir=args.output_dir, wave_index=args.wave_index)
    print(json.dumps({"event": "wave_join_inputs_complete", "sessions": 20,
                      "wave_index": args.wave_index,
                      "manifest": str(args.output_dir / "manifest.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

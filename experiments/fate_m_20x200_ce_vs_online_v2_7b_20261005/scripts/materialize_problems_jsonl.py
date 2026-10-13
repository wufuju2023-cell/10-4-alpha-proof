#!/usr/bin/env python3
"""Materialize the pinned 4000-row JSONL from its repository-sized gzip."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path


HERE = Path(__file__).resolve()
EXPERIMENT_ROOT = HERE.parents[1]
EXPECTED_SHA256 = "3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d"
EXPECTED_ROWS = 4000


def source_bytes(path: Path) -> bytes:
    payload = path.read_bytes()
    return gzip.decompress(payload) if path.suffix == ".gz" else payload


def materialize(
    source: Path,
    output: Path,
    *,
    expected_sha256: str = EXPECTED_SHA256,
    expected_rows: int = EXPECTED_ROWS,
) -> Path:
    payload = source_bytes(source)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"problems JSONL hash mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    if len(payload.splitlines()) != expected_rows:
        raise ValueError(f"problems JSONL must contain exactly {expected_rows} rows")
    if output.exists():
        if output.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace noncanonical output: {output}")
        return output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=EXPERIMENT_ROOT / "data" / "problems.jsonl.gz",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/fate-m-20x200-problems.jsonl"),
    )
    args = parser.parse_args()
    print(materialize(args.source.resolve(), args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

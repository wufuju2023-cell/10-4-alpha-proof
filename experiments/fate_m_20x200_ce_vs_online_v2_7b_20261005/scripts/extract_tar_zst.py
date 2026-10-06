#!/usr/bin/env python3
"""Safely stream-extract a .tar.zst without a standalone zstd binary."""

from __future__ import annotations

import argparse
import pathlib
import tarfile
import time

import zstandard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=pathlib.Path)
    parser.add_argument("destination", type=pathlib.Path)
    parser.add_argument("--expected-root", required=True)
    args = parser.parse_args()

    archive = args.archive.resolve(strict=True)
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    count = 0

    with archive.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                for member in tar:
                    path = pathlib.PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError(f"unsafe archive path: {member.name!r}")
                    if not path.parts or path.parts[0] != args.expected_root:
                        raise RuntimeError(f"unexpected archive root: {member.name!r}")
                    tar.extract(member, destination, filter="data")
                    count += 1
                    if count % 1000 == 0:
                        print(
                            f"[extract-heartbeat] members={count} "
                            f"elapsed_seconds={time.monotonic() - start:.1f}",
                            flush=True,
                        )

    print(
        f"[extract-complete] members={count} "
        f"elapsed_seconds={time.monotonic() - start:.1f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

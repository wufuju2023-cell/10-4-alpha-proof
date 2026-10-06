"""Create a content-addressed receipt for a built patched Reap runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_tree_hash(root: Path) -> str:
    records: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in {".git", ".lake"} for part in path.relative_to(root).parts):
            continue
        if path.suffix == ".lean" or path.name in {"lakefile.toml", "lean-toolchain", "lake-manifest.json"}:
            records.append(f"{path.relative_to(root).as_posix()}\0{sha256_file(path)}\n")
    return hashlib.sha256("".join(records).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reap-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--patch-dir", type=Path, required=True)
    parser.add_argument("--expected-reap-commit", required=True)
    parser.add_argument("--lake-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to replace runtime receipt: {args.output}")
    lake_bin = args.lake_bin.resolve()
    if not lake_bin.is_file() or not os.access(lake_bin, os.X_OK):
        raise SystemExit(f"pinned lake executable is missing or not executable: {lake_bin}")
    commit = subprocess.run(
        ["git", "-C", str(args.reap_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if commit != args.expected_reap_commit:
        raise SystemExit("Reap commit mismatch")
    lean_version = subprocess.run(
        [str(lake_bin), "env", "lean", "--version"], cwd=args.runtime_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    lean_githash = subprocess.run(
        [str(lake_bin), "env", "lean", "-g"], cwd=args.runtime_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    patches = {path.name: sha256_file(path) for path in sorted(args.patch_dir.glob("*.patch"))}
    receipt = {
        "schema_version": "fate.reap.runtime.v1", "reap_commit": commit,
        "reap_source_tree_sha256": source_tree_hash(args.reap_dir),
        "runtime_source_tree_sha256": source_tree_hash(args.runtime_dir),
        "reap_lake_manifest_sha256": sha256_file(args.reap_dir / "lake-manifest.json"),
        "runtime_lake_manifest_sha256": sha256_file(args.runtime_dir / "lake-manifest.json"),
        "lean_toolchain_sha256": sha256_file(args.runtime_dir / "lean-toolchain"),
        "lean_version": lean_version, "lean_githash": lean_githash,
        "lake_executable": str(lake_bin),
        "lake_executable_sha256": sha256_file(lake_bin),
        "patches": patches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", suffix=".tmp", dir=args.output.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.output)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    print(json.dumps({"runtime_receipt": str(args.output.resolve()),
                      "sha256": hashlib.sha256(payload).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

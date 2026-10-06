#!/usr/bin/env python3
"""Build and verify a portable delivery from committed code and immutable receipts."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    experiment = args.evidence_root.resolve()
    assert not args.output.exists(), "delivery is immutable; choose a new output name"
    assert not subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=repo).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    payload = {}
    code = subprocess.check_output(["git", "archive", "--format=zip", "--prefix=code/", "HEAD"], cwd=repo)
    with zipfile.ZipFile(io.BytesIO(code)) as archive:
        for name in archive.namelist():
            if not name.endswith("/"):
                assert not Path(name).is_absolute() and ".." not in Path(name).parts
                payload[name] = archive.read(name)
    prefix = "code/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/"
    payload["DELIVERY.txt"] = payload[prefix + "derived/results/delivery_summary_20261006.txt"]
    evidence = {
        "corrected_final_evidence_20261006.zip": ("runs/formal-20x10-corrected/final-evidence", "b3e9514f179d18c1ab2beb5cf2316a7d6c029c2de369e04e52607148c3aa52d1"),
        "join_v2_evidence_20261006.zip": ("runs/formal-20x10/join-v2", "3924d9605d8b6578717eee0c9dd49f1643b3fa1d0872001dbf2e08130f27df3c"),
        "training_initial_evidence_20261006.zip": ("runs/formal-20x10/training-initial-evidence", "c7909584eea2b643ea49aa8b21b512d9e19df61101bed391c997439e292bce73"),
    }
    scanned = []
    secret = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}|\bhf_[A-Za-z0-9]{25,}|\bAKIA[0-9A-Z]{16}\b|https?://[^\s\"<>]+[?&](?:access_token|refresh_token|code|token)=[A-Za-z0-9._-]{20,}")
    for name, (directory, expected) in evidence.items():
        raw = (experiment / directory / name).read_bytes()
        assert sha(raw) == expected, name
        payload["evidence/" + name] = raw
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            index = json.loads(archive.read("INDEX.json"))
            index = index.get("files", index)
            for source, entry in index.items():
                member_name = entry["member"] if isinstance(entry, dict) else "remote/" + source.lstrip("/")
                expected_hash = entry["sha256"] if isinstance(entry, dict) else entry
                member = archive.read(member_name)
                assert sha(member) == expected_hash, source
            for member in archive.namelist():
                assert not secret.search(archive.read(member).decode("utf-8", errors="ignore")), member
            scanned.append({"name": name, "verified_index_members": len(index), "sha256": expected})
    for name, raw in payload.items():
        if not name.endswith(".zip"):
            assert not secret.search(raw.decode("utf-8", errors="ignore")), name
    manifest = {"schema": "fate.delivery.bundle.v1", "pr": "https://github.com/wufuju2023-cell/10-4-alpha-proof/pull/1",
                "code_commit": head, "evidence_verification": scanned,
                "files": {name: {"bytes": len(raw), "sha256": sha(raw)} for name, raw in sorted(payload.items())},
                "scope": "20 training tasks/one wave; Online rejected and initial evidence reused; all40 heldout direct_target scaffolds",
                "omitted": "Model/checkpoint binaries and signing private keys; checkpoint hashes/remote paths retained in evidence."}
    payload["MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 6, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, raw)
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        for name, entry in manifest["files"].items():
            raw = archive.read(name)
            assert len(raw) == entry["bytes"] and sha(raw) == entry["sha256"], name
    checksum = sha(args.output.read_bytes())
    args.output.with_suffix(".zip.sha256").write_text(checksum + "  " + args.output.name + "\n", encoding="utf-8")
    print(json.dumps({"state": "VERIFIED", "path": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "sha256": checksum, "code_commit": head, "files": len(payload), "evidence": scanned}))


if __name__ == "__main__":
    main()

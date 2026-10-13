#!/usr/bin/env python3
"""Build or verify the frozen 20-family x 10-variant experiment subset."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


EXPERIMENT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = EXPERIMENT_ROOT / "config" / "subset_20x10_protocol.frozen.json"


class ProtocolError(ValueError):
    """Raised when the source or frozen split violates the protocol."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (config_path.parent / candidate).resolve()


def load_protocol(config_path: Path) -> tuple[dict[str, Any], bytes]:
    raw = config_path.read_bytes()
    config = json.loads(raw.decode("utf-8"))
    if config.get("status") != "frozen_before_execution":
        raise ProtocolError("protocol must have status=frozen_before_execution")
    if config.get("execution_started") is not False:
        raise ProtocolError("selection build is only valid before execution_started changes")
    return config, raw


def load_and_validate_source(
    config: dict[str, Any], config_path: Path
) -> tuple[Path, bytes, list[tuple[int, bytes, dict[str, Any]]]]:
    source_spec = config["source"]
    source_path = resolve_from_config(config_path, source_spec["path"])
    if source_path.is_file():
        source_bytes = source_path.read_bytes()
    else:
        compressed_path = source_path.with_name(f"{source_path.name}.gz")
        if not compressed_path.is_file():
            raise FileNotFoundError(f"source JSONL missing: {source_path}")
        source_bytes = gzip.decompress(compressed_path.read_bytes())
    actual_sha = sha256_bytes(source_bytes)
    expected_sha = source_spec["sha256"].lower()
    if actual_sha != expected_sha:
        raise ProtocolError(f"source sha256 mismatch: expected {expected_sha}, got {actual_sha}")

    raw_lines = source_bytes.splitlines(keepends=True)
    if len(raw_lines) != source_spec["expected_rows"]:
        raise ProtocolError(
            f"source row count mismatch: expected {source_spec['expected_rows']}, got {len(raw_lines)}"
        )

    family_key = source_spec["family_key"]
    variant_key = source_spec["variant_key"]
    id_key = source_spec["id_key"]
    record_hash_key = source_spec["record_sha256_key"]
    id_re = re.compile(source_spec["id_pattern"])
    parsed: list[tuple[int, bytes, dict[str, Any]]] = []
    ids: set[str] = set()
    coordinates: set[tuple[int, int]] = set()
    variants_by_family: dict[int, set[int]] = defaultdict(set)

    for source_line, raw_line in enumerate(raw_lines, start=1):
        line_without_eol = raw_line.rstrip(b"\r\n")
        try:
            row = json.loads(line_without_eol.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid UTF-8 JSON at source line {source_line}: {exc}") from exc

        for key in (family_key, variant_key, id_key, record_hash_key, "fate_id", "formal_statement"):
            if key not in row:
                raise ProtocolError(f"source line {source_line} is missing {key!r}")
        family = row[family_key]
        variant = row[variant_key]
        problem_id = row[id_key]
        if not isinstance(family, int) or not isinstance(variant, int):
            raise ProtocolError(f"non-integer family/variant at source line {source_line}")
        match = id_re.fullmatch(problem_id)
        if match is None:
            raise ProtocolError(f"id does not match frozen pattern at source line {source_line}: {problem_id}")
        if int(match.group("fate_id")) != row["fate_id"] or int(match.group("variant")) != variant:
            raise ProtocolError(f"id fields disagree at source line {source_line}: {problem_id}")
        if problem_id in ids:
            raise ProtocolError(f"duplicate id: {problem_id}")
        coordinate = (family, variant)
        if coordinate in coordinates:
            raise ProtocolError(f"duplicate family/variant coordinate: {coordinate}")
        statement_sha = sha256_bytes(row["formal_statement"].encode("utf-8"))
        if statement_sha != str(row[record_hash_key]).lower():
            raise ProtocolError(f"formal_statement sha256 mismatch for {problem_id}")

        ids.add(problem_id)
        coordinates.add(coordinate)
        variants_by_family[family].add(variant)
        parsed.append((source_line, raw_line, row))

    expected_families = source_spec["expected_families"]
    expected_variants = source_spec["expected_variants_per_family"]
    if len(variants_by_family) != expected_families:
        raise ProtocolError(
            f"family count mismatch: expected {expected_families}, got {len(variants_by_family)}"
        )
    expected_variant_set = set(range(1, expected_variants + 1))
    for family, variants in sorted(variants_by_family.items()):
        if variants != expected_variant_set:
            missing = sorted(expected_variant_set - variants)
            extra = sorted(variants - expected_variant_set)
            raise ProtocolError(f"family {family} variant grid mismatch; missing={missing}, extra={extra}")

    return source_path, source_bytes, parsed


def build_artifacts(
    config: dict[str, Any], config_path: Path, config_bytes: bytes
) -> dict[str, bytes]:
    source_path, source_bytes, parsed = load_and_validate_source(config, config_path)
    source_spec = config["source"]
    selection = config["selection"]
    family_key = source_spec["family_key"]
    variant_key = source_spec["variant_key"]
    id_key = source_spec["id_key"]
    selected_variants = set(selection["selected_variant_indices"])
    train_variants = set(selection["train_variant_indices"])
    heldout_variants = set(selection["heldout_variant_indices"])

    if train_variants & heldout_variants:
        raise ProtocolError("train and heldout variant sets overlap")
    if train_variants | heldout_variants != selected_variants:
        raise ProtocolError("train and heldout variants must partition selected variants")

    selected = [item for item in parsed if item[2][variant_key] in selected_variants]
    selected.sort(key=lambda item: (item[2][variant_key], item[2][family_key]))
    train = [item for item in selected if item[2][variant_key] in train_variants]
    heldout = [item for item in selected if item[2][variant_key] in heldout_variants]

    expected_counts = {
        "selected": selection["expected_selected_rows"],
        "train": selection["expected_train_rows"],
        "heldout": selection["expected_heldout_rows"],
    }
    actual_counts = {"selected": len(selected), "train": len(train), "heldout": len(heldout)}
    if actual_counts != expected_counts:
        raise ProtocolError(f"selected split counts mismatch: expected {expected_counts}, got {actual_counts}")

    family_count = source_spec["expected_families"]
    for split_name, split_rows, variant_set in (
        ("train", train, train_variants),
        ("heldout", heldout, heldout_variants),
    ):
        counts = Counter(row[family_key] for _, _, row in split_rows)
        if len(counts) != family_count or set(counts.values()) != {len(variant_set)}:
            raise ProtocolError(f"{split_name} is not balanced across all {family_count} families")

    train_ids = {row[id_key] for _, _, row in train}
    heldout_ids = {row[id_key] for _, _, row in heldout}
    if train_ids & heldout_ids:
        raise ProtocolError("train and heldout ids overlap")

    manifest_rows: list[dict[str, Any]] = []
    split_ranks = {"train": 0, "heldout": 0}
    for selection_rank, (source_line, _, row) in enumerate(selected, start=1):
        split = "train" if row[variant_key] in train_variants else "heldout"
        split_ranks[split] += 1
        manifest_rows.append(
            {
                "selection_rank": selection_rank,
                "split": split,
                "split_rank": split_ranks[split],
                "wave_variant_index": row[variant_key],
                "source_line": source_line,
                "id": row[id_key],
                "family_index": row[family_key],
                "fate_id": row["fate_id"],
                "variant_index": row[variant_key],
                "record_sha256": row[source_spec["record_sha256_key"]],
            }
        )

    train_bytes = b"".join(raw if raw.endswith((b"\n", b"\r")) else raw + b"\n" for _, raw, _ in train)
    heldout_bytes = b"".join(
        raw if raw.endswith((b"\n", b"\r")) else raw + b"\n" for _, raw, _ in heldout
    )
    manifest_bytes = jsonl_bytes(manifest_rows)
    families = sorted({row[family_key] for _, _, row in selected})
    family_fate_ids = [
        {
            "family_index": family,
            "fate_id": next(row["fate_id"] for _, _, row in selected if row[family_key] == family),
        }
        for family in families
    ]
    receipt = {
        "schema_version": 1,
        "protocol_id": config["protocol_id"],
        "status": "PASS",
        "source": {
            "path_from_protocol": source_spec["path"],
            "sha256": sha256_bytes(source_bytes),
            "rows": len(parsed),
        },
        "protocol_sha256": sha256_bytes(config_bytes),
        "counts": actual_counts,
        "family_fate_ids": family_fate_ids,
        "train_variant_indices": sorted(train_variants),
        "heldout_variant_indices": sorted(heldout_variants),
        "train_ids_sha256": sha256_bytes(("\n".join(row[id_key] for _, _, row in train) + "\n").encode()),
        "heldout_ids_sha256": sha256_bytes(
            ("\n".join(row[id_key] for _, _, row in heldout) + "\n").encode()
        ),
        "artifacts": {
            "manifest": {"rows": len(manifest_rows), "sha256": sha256_bytes(manifest_bytes)},
            "train_records": {"rows": len(train), "sha256": sha256_bytes(train_bytes)},
            "heldout_records": {"rows": len(heldout), "sha256": sha256_bytes(heldout_bytes)},
        },
        "invariants": {
            "all_20_families_in_both_splits": True,
            "ten_selected_variants_per_family": True,
            "eight_train_variants_per_family": True,
            "two_heldout_variants_per_family": True,
            "train_heldout_disjoint": True,
            "variant_then_family_order": True,
            "source_record_hashes_verified": True,
        },
    }
    return {
        "manifest": manifest_bytes,
        "train_records": train_bytes,
        "heldout_records": heldout_bytes,
        "selection_receipt": json_bytes(receipt),
    }


def artifact_paths(config: dict[str, Any], config_path: Path, output_dir: Path | None) -> dict[str, Path]:
    configured = config["generated_artifacts"]
    if output_dir is None:
        return {name: resolve_from_config(config_path, path) for name, path in configured.items()}
    return {name: output_dir.resolve() / Path(path).name for name, path in configured.items()}


def write_or_check(artifacts: dict[str, bytes], paths: dict[str, Path], check: bool) -> None:
    for name, expected in artifacts.items():
        path = paths[name]
        if check:
            if not path.is_file():
                raise ProtocolError(f"missing generated artifact: {path}")
            actual = path.read_bytes()
            if actual != expected:
                raise ProtocolError(
                    f"generated artifact drift: {path}; expected sha256={sha256_bytes(expected)}, "
                    f"got sha256={sha256_bytes(actual)}"
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed artifacts byte-for-byte instead of writing them",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = args.config.resolve()
    try:
        config, config_bytes = load_protocol(config_path)
        artifacts = build_artifacts(config, config_path, config_bytes)
        paths = artifact_paths(config, config_path, args.output_dir)
        write_or_check(artifacts, paths, args.check)
    except (OSError, KeyError, TypeError, ProtocolError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        return 1

    action = "verified" if args.check else "wrote"
    print(
        f"PASS: {action} frozen subset artifacts; selected=200 train=160 heldout=40 "
        f"protocol={config['protocol_id']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry points for the CE arm's data boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .budgets import normalize_shared_budget
from .course import prepare_course, prepare_subset_course
from .locks import _write_new, generate_asset_lock, generate_tokenizer_lock
from .replay import ReplayPolicy, build_replay, object_hash, sha256_file


def _lock(config: dict, name: str) -> tuple[Path, str]:
    entry = config["locks"][name]
    path = Path(entry["path"])
    expected = str(entry["sha256"])
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{name} lock hash mismatch: expected {expected}, got {actual}")
    return path, actual


def _verify_tokenizer_lock(path: Path, model_root: str) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if int(lock.get("schema_version", 0)) != 1:
        raise ValueError("unsupported tokenizer lock schema")
    root = Path(lock.get("model_root", "")).resolve()
    if root != Path(model_root).resolve():
        raise ValueError("tokenizer lock model_root differs from configured base model")
    files = lock.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("tokenizer lock has no files")
    names = set()
    for entry in files:
        asset = Path(entry["path"]).resolve()
        if root not in asset.parents or not asset.is_file():
            raise ValueError(f"tokenizer lock path is missing/outside model_root: {asset}")
        if asset.stat().st_size != int(entry["size"]) or sha256_file(asset) != entry["sha256"]:
            raise ValueError(f"tokenizer asset differs from lock: {asset}")
        names.add(asset.name)
    if not {"tokenizer.json", "tokenizer_config.json"}.issubset(names):
        raise ValueError("tokenizer lock must include tokenizer.json and tokenizer_config.json")
    identity = lock.get("receipt_identity_manifest")
    if not isinstance(identity, list) or not identity:
        raise ValueError("tokenizer lock lacks receipt_identity_manifest")
    expected_identity = []
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                 "added_tokens.json", "vocab.json", "merges.txt"):
        asset = root / name
        if asset.is_file():
            expected_identity.append({"path": name, "bytes": asset.stat().st_size,
                                      "sha256": sha256_file(asset)})
    if identity != expected_identity or lock.get("receipt_identity_sha256") != object_hash(identity):
        raise ValueError("tokenizer receipt identity differs from locked tokenizer files")
    return lock


def _receipt_identity(config: dict, name: str) -> str:
    value = config["locks"][name].get("receipt_identity_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} lacks receipt_identity_sha256")
    return value


def build_replay_from_config(config_path: str | Path, receipts: str | Path,
                             output: str | Path, max_wave: int, *,
                             expected_config_sha256: str | None = None) -> dict:
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("status") != "frozen":
        raise ValueError("formal replay publication requires a frozen config")
    config_hash = sha256_file(config_path)
    if expected_config_sha256 is not None and config_hash != expected_config_sha256:
        raise ValueError("frozen config hash differs from expected config hash")
    course_manifest, course_hash = _lock(config, "course_manifest")
    actor_path, _ = _lock(config, "shared_actor_config")
    budget_path, budget_hash = _lock(config, "shared_budget_config")
    tokenizer_path, tokenizer_hash = _lock(config, "tokenizer_lock")
    verifier_path, verifier_hash = _lock(config, "trusted_verifier_lock")
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    budget_limits = normalize_shared_budget(budget)
    verifier_lock = json.loads(verifier_path.read_text(encoding="utf-8"))
    actor_identity = object_hash(json.loads(actor_path.read_text(encoding="utf-8")))
    if actor_identity != _receipt_identity(config, "shared_actor_config"):
        raise ValueError("shared actor receipt identity differs from the locked actor config")
    tokenizer_lock = _verify_tokenizer_lock(tokenizer_path, config["model"]["base_path"])
    tokenizer_identity = _receipt_identity(config, "tokenizer_lock")
    if tokenizer_lock["receipt_identity_sha256"] != tokenizer_identity:
        raise ValueError("tokenizer receipt identity differs from frozen config")
    policy = ReplayPolicy(
        course_manifest_path=course_manifest,
        course_manifest_sha256=course_hash,
        ce_config_sha256=config_hash,
        max_wave=max_wave,
        actor_config_sha256=actor_identity,
        budget_config_sha256=budget_hash,
        tokenizer_lock_sha256=tokenizer_identity,
        trusted_verifier_id=config["locks"]["trusted_verifier_lock"]["verifier_id"],
        trusted_verifier_lock_sha256=verifier_hash,
        trusted_verifier_public_key_hex=verifier_lock["ed25519_public_key_hex"],
        max_attempts_per_problem=budget_limits["max_attempts_per_problem"],
        max_generated_tokens_per_problem=budget_limits[
            "max_generated_tokens_per_problem"
        ],
        max_lean_tactic_executions_per_problem=int(
            budget_limits["max_lean_tactic_executions_per_problem"]
        ),
        value_bins=int(config["model"]["value_bins"]),
        distance_overflow_policy=config["replay"]["distance_overflow_policy"],
    )
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        config["model"]["base_path"], local_files_only=True
    )
    return build_replay(receipts, output, policy=policy, tokenizer=tokenizer)


def bind_config(template: str | Path, output: str | Path, locks: dict[str, str | Path]) -> dict:
    """Materialize one immutable CE config and print its launch hash."""
    value = json.loads(Path(template).read_text(encoding="utf-8"))
    if value.get("status") != "template" or value.get("run_scope") not in {
        "real_7b_one_update_smoke", "subset_20x10_formal",
    }:
        raise ValueError("bind-config requires an approved CE smoke or subset formal template")
    for name, supplied in locks.items():
        path = Path(supplied).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{name} lock is missing: {path}")
        value["locks"][name] = {"path": str(path), "sha256": sha256_file(path)}
    actor_path = Path(value["locks"]["shared_actor_config"]["path"])
    actor_config = json.loads(actor_path.read_text(encoding="utf-8"))
    if not isinstance(actor_config, dict):
        raise ValueError("shared actor config must be a JSON object")
    value["locks"]["shared_actor_config"]["receipt_identity_sha256"] = object_hash(
        actor_config
    )
    tokenizer_path = Path(value["locks"]["tokenizer_lock"]["path"])
    tokenizer_lock = _verify_tokenizer_lock(tokenizer_path, value["model"]["base_path"])
    value["locks"]["tokenizer_lock"]["receipt_identity_sha256"] = tokenizer_lock[
        "receipt_identity_sha256"
    ]
    verifier_path = Path(value["locks"]["trusted_verifier_lock"]["path"])
    verifier = json.loads(verifier_path.read_text(encoding="utf-8"))
    verifier_id = verifier.get("verifier_id")
    if not isinstance(verifier_id, str) or not verifier_id:
        raise ValueError("trusted verifier lock lacks verifier_id")
    value["locks"]["trusted_verifier_lock"]["verifier_id"] = verifier_id
    value["status"] = "frozen"
    _write_new(Path(output), value)
    return {"config": str(Path(output).resolve()), "sha256": sha256_file(output),
            "status": "frozen", "run_scope": value["run_scope"]}


bind_smoke_config = bind_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare-course", help="validate 20x200 and derive waves/splits")
    prep.add_argument("--source", required=True)
    prep.add_argument("--output", required=True)
    subset = sub.add_parser("prepare-subset-course", help="publish frozen 20x10 train/heldout waves")
    subset.add_argument("--train", required=True)
    subset.add_argument("--heldout", required=True)
    subset.add_argument("--selection-receipt", required=True)
    subset.add_argument("--output", required=True)
    replay = sub.add_parser("build-replay", help="convert canonical verified receipts")
    replay.add_argument("--config", required=True)
    replay.add_argument("--receipts", required=True)
    replay.add_argument("--output", required=True)
    replay.add_argument("--max-wave", required=True, type=int)
    replay.add_argument("--expected-config-sha256")
    bind = sub.add_parser("bind-smoke-config", help="bind all immutable locks to the one-update template")
    bind.add_argument("--template", required=True)
    bind.add_argument("--output", required=True)
    for lock_name in ("asset-lock", "course-manifest", "shared-actor-config",
                      "shared-budget-config", "tokenizer-lock", "trusted-verifier-lock"):
        bind.add_argument(f"--{lock_name}", required=True)
    bind_formal = sub.add_parser("bind-config", help="bind all immutable locks to a CE template")
    bind_formal.add_argument("--template", required=True)
    bind_formal.add_argument("--output", required=True)
    for lock_name in ("asset-lock", "course-manifest", "shared-actor-config",
                      "shared-budget-config", "tokenizer-lock", "trusted-verifier-lock"):
        bind_formal.add_argument(f"--{lock_name}", required=True)
    tokenizer_lock = sub.add_parser("generate-tokenizer-lock")
    tokenizer_lock.add_argument("--model-root", required=True)
    tokenizer_lock.add_argument("--model-revision", required=True)
    tokenizer_lock.add_argument("--output", required=True)
    asset_lock = sub.add_parser("generate-asset-lock")
    asset_lock.add_argument("--model-root", required=True)
    asset_lock.add_argument("--model-revision", required=True)
    asset_lock.add_argument("--value-head", required=True)
    asset_lock.add_argument("--initial-adapter", required=True)
    asset_lock.add_argument("--target-repo", required=True)
    asset_lock.add_argument("--target-value-head-source", required=True)
    asset_lock.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if args.command == "prepare-course":
        result = prepare_course(args.source, args.output)
    elif args.command == "prepare-subset-course":
        result = prepare_subset_course(
            args.train, args.heldout, args.selection_receipt, args.output
        )
    elif args.command == "build-replay":
        result = build_replay_from_config(
            args.config, args.receipts, args.output, args.max_wave,
            expected_config_sha256=args.expected_config_sha256,
        )
    elif args.command in {"bind-smoke-config", "bind-config"}:
        result = bind_config(args.template, args.output, {
            "asset_lock": args.asset_lock,
            "course_manifest": args.course_manifest,
            "shared_actor_config": args.shared_actor_config,
            "shared_budget_config": args.shared_budget_config,
            "tokenizer_lock": args.tokenizer_lock,
            "trusted_verifier_lock": args.trusted_verifier_lock,
        })
    elif args.command == "generate-tokenizer-lock":
        result = generate_tokenizer_lock(args.model_root, args.model_revision, args.output)
    else:
        result = generate_asset_lock(
            args.model_root, args.model_revision, args.value_head, args.initial_adapter,
            args.target_repo,
            args.target_value_head_source, args.output,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

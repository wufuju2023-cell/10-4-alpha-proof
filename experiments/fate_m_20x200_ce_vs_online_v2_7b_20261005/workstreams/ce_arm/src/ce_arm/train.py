"""Strict, memory-bounded LoRA trainer for the verified search-path CE arm."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .budgets import normalize_shared_budget


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value: object, excluded_key: str | None = None) -> str:
    if excluded_key is not None:
        value = {key: item for key, item in value.items() if key != excluded_key}  # type: ignore[union-attr]
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _checkpoint_publication_lock(output: Path):
    """Serialize checkpoint reconciliation/publication without stale lock state."""
    checkpoint_root = output / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    lock_path = checkpoint_root / ".publication.lock"
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another process is publishing/reconciling checkpoints") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_train_config(config: dict, *, allow_draft: bool = False) -> None:
    if config.get("status") != "frozen" and not allow_draft:
        raise ValueError("formal training requires status='frozen'; --allow-draft is smoke-only")
    if int(config.get("model", {}).get("value_bins", 0)) != 64:
        raise ValueError("model.value_bins must be exactly 64")
    train = config.get("train", {})
    required = (
        "batch_size", "micro_batch_size", "steps_per_wave", "max_length", "policy_lr",
        "value_lr", "value_coef", "max_grad_norm", "seed", "checkpoint_every_steps",
    )
    missing = [key for key in required if train.get(key) is None]
    if missing:
        raise ValueError(f"unfrozen training fields: {missing}")
    integer_positive = ("batch_size", "micro_batch_size", "steps_per_wave", "max_length",
                        "checkpoint_every_steps")
    for key in integer_positive:
        value = train[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"train.{key} must be a positive integer")
    if train["batch_size"] % train["micro_batch_size"] != 0:
        raise ValueError("batch_size must be divisible by micro_batch_size")
    sampling = config.get("replay", {}).get("sampling")
    if sampling not in {"uniform_over_transitions", "full_replay"}:
        raise ValueError(
            "replay.sampling must be 'uniform_over_transitions' or 'full_replay'"
        )
    for key in ("policy_lr", "value_lr", "value_coef", "max_grad_norm"):
        value = train[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"train.{key} must be finite and positive")
    lora = train.get("lora", {})
    if not isinstance(lora.get("r"), int) or lora["r"] <= 0:
        raise ValueError("LoRA rank must be a positive integer")
    if not isinstance(lora.get("alpha"), int) or lora["alpha"] <= 0:
        raise ValueError("LoRA alpha must be a positive integer")
    dropout = lora.get("dropout")
    if not isinstance(dropout, (int, float)) or not 0 <= dropout < 1:
        raise ValueError("LoRA dropout must be in [0,1)")
    if not lora.get("target_modules") or len(set(lora["target_modules"])) != len(lora["target_modules"]):
        raise ValueError("LoRA target_modules must be a non-empty unique list")
    if config.get("replay", {}).get("distance_overflow_policy") != "reject":
        raise ValueError("distance_overflow_policy must be 'reject'")


def verify_asset_lock(lock_path: str | Path, expected_hash: str, *,
                      runtime_versions: dict[str, str] | None = None) -> dict:
    path = Path(lock_path).resolve()
    actual = sha256_file(path)
    if actual != expected_hash:
        raise ValueError(f"asset lock hash mismatch: expected {expected_hash}, got {actual}")
    lock = json.loads(path.read_text(encoding="utf-8"))
    if int(lock.get("schema_version", 0)) != 1:
        raise ValueError("unsupported asset lock schema")
    roles: dict[str, int] = {}
    for entry in lock.get("files", []):
        file_path = Path(entry["path"]).resolve()
        if not file_path.is_file():
            raise ValueError(f"locked asset is missing: {file_path}")
        if int(entry["size"]) != file_path.stat().st_size:
            raise ValueError(f"locked asset size mismatch: {file_path}")
        if entry["sha256"] != sha256_file(file_path):
            raise ValueError(f"locked asset hash mismatch: {file_path}")
        role = str(entry["role"])
        roles[role] = roles.get(role, 0) + 1
    required_single = {
        "model_config", "generation_config", "tokenizer_config", "tokenizer",
        "model_index", "value_head", "target_value_head_source",
        "initial_adapter_config", "initial_adapter_model", "initial_adapter_manifest",
    }
    missing = sorted(role for role in required_single if roles.get(role, 0) != 1)
    if missing or roles.get("model_shard", 0) < 1:
        raise ValueError(f"asset lock has incomplete/ambiguous required roles: {missing}; model_shards={roles.get('model_shard', 0)}")
    model_root = Path(lock.get("model_root", "")).resolve()
    if not model_root.is_dir():
        raise ValueError("asset lock model_root is missing")
    model_revision = lock.get("model_revision")
    if not isinstance(model_revision, str) or len(model_revision) != 40:
        raise ValueError("asset lock model_revision must be a full 40-hex revision")
    try:
        int(model_revision, 16)
    except ValueError as exc:
        raise ValueError("asset lock model_revision is not hexadecimal") from exc
    model_roles = {"model_config", "generation_config", "tokenizer_config", "tokenizer",
                   "model_index", "model_shard", "model_aux"}
    locked_model_files = {
        str(Path(entry["path"]).resolve()) for entry in lock["files"]
        if entry["role"] in model_roles
    }
    actual_model_files = {str(item.resolve()) for item in model_root.rglob("*") if item.is_file()}
    if locked_model_files != actual_model_files:
        raise ValueError("asset lock is not a complete inventory of model_root")
    index_path = next(Path(entry["path"]) for entry in lock["files"]
                      if entry["role"] == "model_index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    indexed_shards = {str((model_root / name).resolve())
                      for name in index.get("weight_map", {}).values()}
    locked_shards = {str(Path(entry["path"]).resolve()) for entry in lock["files"]
                     if entry["role"] == "model_shard"}
    if not indexed_shards or indexed_shards != locked_shards:
        raise ValueError("asset lock model_shards do not exactly match model_index.weight_map")
    adapter_root = Path(lock.get("initial_adapter_root", "")).resolve()
    if not adapter_root.is_dir():
        raise ValueError("asset lock initial_adapter_root is missing")
    locked_adapter_files = {
        str(Path(entry["path"]).resolve()) for entry in lock["files"]
        if str(entry["role"]).startswith("initial_adapter_")
    }
    actual_adapter_files = {
        str(item.resolve()) for item in adapter_root.rglob("*") if item.is_file()
    }
    if locked_adapter_files != actual_adapter_files:
        raise ValueError("asset lock is not a complete inventory of initial_adapter_root")
    git_lock = lock.get("target_git", {})
    repo = Path(git_lock.get("path", ""))
    if not repo.is_dir():
        raise ValueError("asset lock target_git.path is missing")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    if commit != git_lock.get("commit") or dirty:
        raise ValueError("target repository commit/clean-state differs from the asset lock")
    if runtime_versions is not None:
        locked_versions = lock.get("runtime_versions", {})
        required_versions = {"torch", "transformers", "peft", "cryptography"}
        if set(locked_versions) != required_versions:
            raise ValueError("asset lock must pin exactly torch/transformers/peft/cryptography")
        for package, expected in locked_versions.items():
            if runtime_versions.get(package) != expected:
                raise ValueError(
                    f"runtime version mismatch for {package}: expected {expected}, "
                    f"got {runtime_versions.get(package)}"
                )
    return lock


def verify_initial_adapter(config: dict, asset_lock: dict) -> tuple[Path, dict]:
    """Verify the byte-identical shared initialization before PEFT loads it."""
    pin = config.get("model", {}).get("initial_adapter")
    if not isinstance(pin, dict):
        raise ValueError("model.initial_adapter pin is required")
    required = (
        "path", "manifest_payload_sha256", "adapter_model_sha256",
        "adapter_config_sha256", "trainable_state_sha256",
    )
    missing = [name for name in required if not isinstance(pin.get(name), str) or not pin[name]]
    if missing:
        raise ValueError(f"model.initial_adapter has unfrozen fields: {missing}")
    root = Path(pin["path"]).resolve()
    if root != Path(asset_lock.get("initial_adapter_root", "")).resolve():
        raise ValueError("configured initial adapter differs from the asset lock")
    manifest_path = root / "formal_initial_adapter_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "fate-m.formal-initial-lora.v1":
        raise ValueError("unsupported formal initial adapter manifest")
    if manifest.get("manifest_payload_sha256") != object_hash(
            manifest, "manifest_payload_sha256"):
        raise ValueError("initial adapter manifest payload hash mismatch")
    exact = {
        "manifest_payload_sha256": manifest.get("manifest_payload_sha256"),
        "adapter_model_sha256": sha256_file(root / "adapter_model.safetensors"),
        "adapter_config_sha256": sha256_file(root / "adapter_config.json"),
        "trainable_state_sha256": manifest.get("trainable_state_sha256"),
    }
    for field, actual in exact.items():
        if pin[field] != actual:
            raise ValueError(f"initial adapter {field} mismatch")
    if manifest.get("trained") is not False:
        raise ValueError("initial adapter must be the frozen untrained shared adapter")
    if Path(manifest.get("base_model_path", "")).resolve() != Path(
            config["model"]["base_path"]).resolve():
        raise ValueError("initial adapter base_model_path mismatch")
    if manifest.get("base_model_revision") != config["model"]["base_revision"]:
        raise ValueError("initial adapter base model revision mismatch")
    expected_lora = config["train"]["lora"]
    observed_lora = manifest.get("lora", {})
    for config_key, manifest_key in (("r", "r"), ("alpha", "alpha"),
                                     ("dropout", "dropout"),
                                     ("target_modules", "target_modules")):
        if observed_lora.get(manifest_key) != expected_lora.get(config_key):
            raise ValueError(f"initial adapter LoRA {config_key} mismatch")
    for name, entry in manifest.get("files", {}).items():
        path = root / name
        if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
            raise ValueError(f"initial adapter manifest file mismatch: {name}")
        if sha256_file(path) != entry["sha256"]:
            raise ValueError(f"initial adapter manifest hash mismatch: {name}")
    return root, manifest


def _formal_trainable_state_sha256(model, torch) -> str:
    """Reproduce the frozen adapter creator's tensor-state hash domain."""
    digest = hashlib.sha256()
    trainable = sorted((name, value) for name, value in model.named_parameters()
                       if value.requires_grad)
    if not trainable:
        raise ValueError("loaded initial adapter exposes no trainable tensors")
    for name, value in trainable:
        tensor = value.detach().cpu().contiguous()
        entry = {"name": name, "shape": list(tensor.shape), "dtype": str(tensor.dtype)}
        digest.update(json.dumps(
            entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"))
        digest.update(tensor.view(dtype=torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def validate_all_tokenization(tokenizer, rows: list[dict]) -> None:
    for row in rows:
        transition_id = row["extra"]["transition_id"]
        if list(row["extra"]["prompt_token_ids"]) != list(
            tokenizer.encode(row["prompt"], add_special_tokens=True)
        ):
            raise ValueError(f"{transition_id}: prompt IDs/text mismatch")
        decoded = tokenizer.decode(
            list(row["extra"]["action_token_ids"]),
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        if decoded.replace("\r\n", "\n").strip() != str(row["action"]).replace(
                "\r\n", "\n").strip():
            raise ValueError(f"{transition_id}: action IDs/text mismatch")


def verify_declared_locks(config: dict) -> None:
    required = {
        "asset_lock", "course_manifest", "shared_actor_config", "shared_budget_config",
        "tokenizer_lock", "trusted_verifier_lock",
    }
    missing = sorted(required - set(config.get("locks", {})))
    if missing:
        raise ValueError(f"missing frozen lock declarations: {missing}")
    for name in sorted(required):
        entry = config["locks"][name]
        path = Path(entry.get("path", ""))
        expected = entry.get("sha256")
        if not path.is_file() or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"invalid {name} lock declaration")
        if sha256_file(path) != expected:
            raise ValueError(f"declared {name} lock hash mismatch")
    for name in ("shared_actor_config", "tokenizer_lock"):
        _frozen_receipt_identity(config, name)
    actor = json.loads(Path(
        config["locks"]["shared_actor_config"]["path"]
    ).read_text(encoding="utf-8"))
    if object_hash(actor) != _frozen_receipt_identity(config, "shared_actor_config"):
        raise ValueError("shared actor config receipt identity mismatch")
    tokenizer_lock = json.loads(Path(
        config["locks"]["tokenizer_lock"]["path"]
    ).read_text(encoding="utf-8"))
    if tokenizer_lock.get("receipt_identity_sha256") != _frozen_receipt_identity(
            config, "tokenizer_lock"):
        raise ValueError("tokenizer lock receipt identity mismatch")


def _frozen_receipt_identity(config: dict, name: str) -> str:
    value = config["locks"][name].get("receipt_identity_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} lacks frozen receipt_identity_sha256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} receipt_identity_sha256 is not hexadecimal") from exc
    return value


def _load_frozen_verifier_lock(config: dict) -> dict:
    entry = config["locks"]["trusted_verifier_lock"]
    path = Path(entry["path"])
    raw = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != entry["sha256"]:
        raise ValueError(
            "trusted verifier lock file hash does not match the frozen configuration"
        )
    try:
        lock = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("trusted verifier lock is invalid JSON") from exc
    if not isinstance(lock, dict):
        raise ValueError("trusted verifier lock must be a JSON object")
    return lock


def validate_replay_bundle(bundle_dir: str | Path, config: dict, wave_index: int,
                           config_sha256: str) -> tuple[Path, dict, str]:
    bundle = Path(bundle_dir).resolve()
    manifest_path = bundle / "manifest.json"
    transitions_path = bundle / "transitions.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload_hash = manifest.get("manifest_payload_sha256")
    if payload_hash != object_hash(manifest, "manifest_payload_sha256"):
        raise ValueError("replay manifest payload hash mismatch")
    if manifest.get("status") != "complete" or int(manifest.get("schema_version", 0)) != 2:
        raise ValueError("replay bundle is not a complete schema-v2 publication")
    if sha256_file(transitions_path) != manifest.get("transition_sha256"):
        raise ValueError("replay transition file hash mismatch")
    verifier_lock = _load_frozen_verifier_lock(config)
    verifier_public_key_hash = hashlib.sha256(
        bytes.fromhex(verifier_lock["ed25519_public_key_hex"])
    ).hexdigest()
    expected = {
        "max_wave": wave_index,
        "ce_config_sha256": config_sha256,
        "actor_config_sha256": _frozen_receipt_identity(config, "shared_actor_config"),
        "budget_config_sha256": config["locks"]["shared_budget_config"]["sha256"],
        "tokenizer_lock_sha256": _frozen_receipt_identity(config, "tokenizer_lock"),
        "trusted_verifier_id": config["locks"]["trusted_verifier_lock"]["verifier_id"],
        "trusted_verifier_lock_sha256": config["locks"]["trusted_verifier_lock"]["sha256"],
        "trusted_verifier_public_key_sha256": verifier_public_key_hash,
        "course_manifest_sha256": config["locks"]["course_manifest"]["sha256"],
        "value_bins": 64,
        "distance_overflow_policy": "reject",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"replay manifest {key} does not match the frozen run")
    budget = json.loads(Path(
        config["locks"]["shared_budget_config"]["path"]
    ).read_text(encoding="utf-8"))
    budget_limits = normalize_shared_budget(budget)
    if manifest.get("budget_limits") != budget_limits:
        raise ValueError("replay budget limits do not match the shared budget lock")
    costs = manifest.get("per_problem_costs")
    if not isinstance(costs, dict) or not costs:
        raise ValueError("replay manifest lacks a per-problem cost ledger")
    if manifest.get("per_problem_costs_sha256") != object_hash(costs):
        raise ValueError("replay per-problem cost ledger hash mismatch")
    totals = {"attempts": 0, "generated_tokens": 0, "lean_tactic_executions": 0}
    key_to_limit = {
        "attempts": "max_attempts_per_problem",
        "generated_tokens": "max_generated_tokens_per_problem",
        "lean_tactic_executions": "max_lean_tactic_executions_per_problem",
    }
    for problem_id, counters in costs.items():
        if not isinstance(problem_id, str) or not problem_id or not isinstance(counters, dict):
            raise ValueError("replay has a malformed per-problem cost entry")
        for counter, limit_key in key_to_limit.items():
            value = counters.get(counter)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"replay cost {problem_id}.{counter} is invalid")
            if value > budget_limits[limit_key]:
                raise ValueError(f"replay cost {problem_id}.{counter} exceeds shared budget")
            totals[counter] += value
    stats = manifest.get("stats", {})
    expected_stats = {
        "receipts": totals["attempts"],
        "generated_tokens": totals["generated_tokens"],
        "lean_tactic_executions": totals["lean_tactic_executions"],
    }
    for key, value in expected_stats.items():
        if stats.get(key) != value:
            raise ValueError(f"replay stats.{key} disagrees with per-problem costs")
    return transitions_path, manifest, sha256_file(manifest_path)


def _verify_transition_verifier_signature(verification: dict, public_key_hex: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    payload = {
        key: value for key, value in verification.items()
        if key not in {"verification_receipt_sha256", "signature_hex"}
    }
    payload_hash = object_hash(payload)
    if verification.get("verification_receipt_sha256") != payload_hash:
        raise ValueError("transition verifier receipt hash mismatch")
    signature = verification.get("signature_hex")
    if type(signature) is not str or len(signature) != 128:
        raise ValueError("transition verifier signature is missing or malformed")
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(signature), bytes.fromhex(payload_hash))
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("transition verifier signature check failed") from exc


def load_transition_rows(path: str | Path, *, vocabulary_size: int,
                         actor_config_sha256: str, budget_config_sha256: str,
                         tokenizer_lock_sha256: str, max_wave: int,
                         trusted_verifier_id: str,
                         trusted_verifier_lock_sha256: str,
                         trusted_verifier_public_key_hex: str,
                         value_bins: int = 64) -> list[dict]:
    rows = []
    transition_ids: set[str] = set()
    path_groups: dict[tuple[str, str], dict[str, object]] = {}
    receipt_requests: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            prefix = f"{path}:{line_number}"
            if row.get("terminal_verified") is not True or row.get("solved") is not True:
                raise ValueError(f"{prefix}: CE transition must be solved and verified")
            if row.get("kind") not in {"proof", "disproof"}:
                raise ValueError(f"{prefix}: invalid transition kind")
            if not isinstance(row.get("prompt"), str) or not row["prompt"].strip():
                raise ValueError(f"{prefix}: prompt is empty")
            if not isinstance(row.get("action"), str) or not row["action"].strip():
                raise ValueError(f"{prefix}: action is empty")
            extra = row.get("extra")
            if not isinstance(extra, dict):
                raise ValueError(f"{prefix}: missing transition provenance")
            path_index = extra.get("path_index")
            path_length = extra.get("path_length")
            if (type(path_index) is not int or type(path_length) is not int
                    or not 1 <= path_length <= value_bins
                    or not 0 <= path_index < path_length):
                raise ValueError(
                    f"{prefix}: path_index/path_length must define a position in a "
                    f"verified path of length 1..{value_bins}"
                )
            derived_target = -(path_length - path_index)
            target = row.get("value_target")
            if type(target) not in {int, float} or target != derived_target:
                raise ValueError(
                    f"{prefix}: value_target mismatch: row reports {target!r}, "
                    f"path_index/path_length derive {derived_target}"
                )
            transition_id = str(extra.get("transition_id", ""))
            if len(transition_id) != 64 or transition_id in transition_ids:
                raise ValueError(f"{prefix}: missing/duplicate transition_id")
            transition_ids.add(transition_id)
            receipt_id = extra.get("receipt_id")
            request_sha256 = extra.get("request_sha256")
            if (type(receipt_id) is not str or not receipt_id
                    or type(request_sha256) is not str or len(request_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in request_sha256)):
                raise ValueError(f"{prefix}: missing receipt/path provenance")
            prior_request = receipt_requests.setdefault(receipt_id, request_sha256)
            if prior_request != request_sha256:
                raise ValueError(f"{prefix}: one receipt_id is bound to multiple requests")
            group = path_groups.setdefault(
                (receipt_id, request_sha256),
                {
                    "path_length": path_length,
                    "indices": set(),
                    "steps": {},
                    "verification": extra.get("verification"),
                    "selected_path_sha256": extra.get("selected_path_sha256"),
                    "verifier_receipt_sha256": extra.get("verifier_receipt_sha256"),
                },
            )
            if group["path_length"] != path_length:
                raise ValueError(f"{prefix}: inconsistent path_length within one receipt")
            indices = group["indices"]
            assert isinstance(indices, set)
            if path_index in indices:
                raise ValueError(f"{prefix}: duplicate path_index within one receipt")
            indices.add(path_index)
            step = extra.get("selected_path_step")
            if not isinstance(step, dict):
                raise ValueError(f"{prefix}: missing signed selected_path_step")
            expected_step_fields = {
                "step_index": path_index,
                "state_before_sha256": extra.get("state_before_sha256"),
                "state_after_sha256": extra.get("state_after_sha256"),
                "prompt": row["prompt"],
                "prompt_token_ids": extra.get("prompt_token_ids"),
                "action": row["action"],
                "value_target": target,
            }
            for key, expected_value in expected_step_fields.items():
                if step.get(key) != expected_value:
                    raise ValueError(f"{prefix}: selected_path_step drift for {key}")
            raw_ids = step.get("raw_completion_token_ids")
            span = step.get("tactic_token_span")
            if (not isinstance(raw_ids, list) or not raw_ids
                    or any(type(token) is not int or not 0 <= token < vocabulary_size for token in raw_ids)
                    or not isinstance(span, list) or len(span) != 2
                    or any(type(item) is not int for item in span)
                    or not 0 <= span[0] < span[1] <= len(raw_ids)
                    or raw_ids[span[0]:span[1]] != extra.get("action_token_ids")):
                raise ValueError(f"{prefix}: signed tactic token span is invalid or drifted")
            steps = group["steps"]
            assert isinstance(steps, dict)
            steps[path_index] = step
            if (group["verification"] != extra.get("verification")
                    or group["selected_path_sha256"] != extra.get("selected_path_sha256")
                    or group["verifier_receipt_sha256"] != extra.get("verifier_receipt_sha256")):
                raise ValueError(f"{prefix}: inconsistent signed path commitment within one receipt")
            if not 1 <= int(extra.get("wave_index", 0)) <= max_wave:
                raise ValueError(f"{prefix}: held-out/future wave transition")
            required_hashes = {
                "actor_config_sha256": actor_config_sha256,
                "budget_config_sha256": budget_config_sha256,
                "tokenizer_lock_sha256": tokenizer_lock_sha256,
            }
            for key, expected in required_hashes.items():
                if extra.get(key) != expected:
                    raise ValueError(f"{prefix}: provenance hash mismatch for {key}")
            for key in ("prompt_token_ids", "action_token_ids"):
                ids = extra.get(key)
                if not isinstance(ids, list) or not ids:
                    raise ValueError(f"{prefix}: {key} must be a non-empty list")
                if any(isinstance(token, bool) or not isinstance(token, int) or
                       not 0 <= token < vocabulary_size for token in ids):
                    raise ValueError(f"{prefix}: {key} contains an invalid token ID")
            expected_transition_id = object_hash({
                "request_sha256": request_sha256,
                "step_index": path_index,
                "before": extra.get("state_before_sha256"),
                "action_token_ids": extra["action_token_ids"],
                "after": extra.get("state_after_sha256"),
            })
            if transition_id != expected_transition_id:
                raise ValueError(f"{prefix}: transition_id is not derived from signed path fields")
            rows.append(row)
    if not rows:
        raise ValueError(f"empty replay: {path}")
    for (receipt_id, request_sha256), group in path_groups.items():
        path_length = int(group["path_length"])
        indices = group["indices"]
        assert isinstance(indices, set)
        expected = set(range(path_length))
        if indices != expected:
            missing = sorted(expected - indices)
            extra_indices = sorted(indices - expected)
            raise ValueError(
                f"{path}: receipt {receipt_id} has incomplete/noncontiguous path rows; "
                f"missing={missing}, extra={extra_indices}"
            )
        steps = group["steps"]
        assert isinstance(steps, dict)
        selected_path = [steps[index] for index in range(path_length)]
        selected_path_sha256 = object_hash(selected_path)
        if group["selected_path_sha256"] != selected_path_sha256:
            raise ValueError(f"{path}: receipt {receipt_id} selected path hash mismatch")
        verification = group["verification"]
        if not isinstance(verification, dict):
            raise ValueError(f"{path}: receipt {receipt_id} lacks signed verifier commitment")
        try:
            _verify_transition_verifier_signature(
                verification, trusted_verifier_public_key_hex
            )
        except ValueError as exc:
            raise ValueError(f"{path}: receipt {receipt_id}: {exc}") from exc
        required_verification = {
            "verifier_id": trusted_verifier_id,
            "verifier_lock_sha256": trusted_verifier_lock_sha256,
            "request_sha256": request_sha256,
            "selected_path_sha256": selected_path_sha256,
            "actor_config_sha256": actor_config_sha256,
            "budget_config_sha256": budget_config_sha256,
            "tokenizer_lock_sha256": tokenizer_lock_sha256,
            "result": "verified",
            "kernel_exit_code": 0,
        }
        for key, expected_value in required_verification.items():
            if verification.get(key) != expected_value:
                raise ValueError(
                    f"{path}: receipt {receipt_id} verifier commitment mismatch for {key}"
                )
        if group["verifier_receipt_sha256"] != verification.get("verification_receipt_sha256"):
            raise ValueError(f"{path}: receipt {receipt_id} verifier receipt provenance mismatch")
        if (verification.get("initial_state_sha256") != selected_path[0]["state_before_sha256"]
                or verification.get("final_state_sha256") != selected_path[-1]["state_after_sha256"]):
            raise ValueError(f"{path}: receipt {receipt_id} signed path endpoints mismatch")
    return rows


def encode_rows(tokenizer, rows: list[dict], max_length: int, device: str):
    """Use the exact receipt-validated prompt and contextual action token IDs."""
    import torch

    encoded = []
    for row in rows:
        prompt_ids = list(row["extra"]["prompt_token_ids"])
        action_ids = list(row["extra"]["action_token_ids"])
        if prompt_ids != list(tokenizer.encode(row["prompt"], add_special_tokens=True)):
            raise ValueError("stored prompt_token_ids no longer match the pinned tokenizer")
        decoded = tokenizer.decode(
            action_ids, skip_special_tokens=False, clean_up_tokenization_spaces=False
        )
        if decoded.replace("\r\n", "\n").strip() != str(row["action"]).replace(
                "\r\n", "\n").strip():
            raise ValueError("stored action_token_ids no longer match the pinned tokenizer")
        full = prompt_ids + action_ids
        if len(full) > max_length:
            raise ValueError(
                f"sequence length {len(full)} exceeds max_length={max_length}; truncation is forbidden"
            )
        encoded.append((full, [-100] * len(prompt_ids) + action_ids, len(prompt_ids)))
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    if pad_id is None:
        raise ValueError("tokenizer has neither pad_token_id nor eos_token_id")
    width = max(len(item[0]) for item in encoded)
    input_ids, labels, attention = [], [], []
    for ids, target, _ in encoded:
        padding = width - len(ids)
        input_ids.append(ids + [pad_id] * padding)
        labels.append(target + [-100] * padding)
        attention.append([1] * len(ids) + [0] * padding)
    return {
        "input_ids": torch.tensor(input_ids, dtype=torch.long, device=device),
        "labels": torch.tensor(labels, dtype=torch.long, device=device),
        "attention_mask": torch.tensor(attention, dtype=torch.long, device=device),
        "prompt_lengths": torch.tensor([item[2] for item in encoded], dtype=torch.long, device=device),
    }


def _forward_policy_and_hidden(model, input_ids, attention_mask):
    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    if hasattr(base, "model") and hasattr(base, "lm_head"):
        decoder = base.model(input_ids=input_ids, attention_mask=attention_mask,
                             use_cache=False, return_dict=True)
        hidden = decoder.last_hidden_state
        return base.lm_head(hidden), hidden
    output = model(input_ids=input_ids, attention_mask=attention_mask,
                   output_hidden_states=True, use_cache=False, return_dict=True)
    return output.logits, output.hidden_states[-1]


def _two_hot(distance, bins: int = 64):
    import torch

    if bool(((distance < 1) | (distance > bins) | ~torch.isfinite(distance)).any().item()):
        raise ValueError(f"distance must be finite in [1,{bins}]")
    lower = distance.float().floor()
    upper = (lower + 1).clamp(max=float(bins))
    fraction = distance.float() - lower
    target = torch.zeros((distance.shape[0], bins), dtype=torch.float32, device=distance.device)
    target.scatter_add_(1, (lower - 1).long().unsqueeze(1), (1 - fraction).unsqueeze(1))
    target.scatter_add_(1, (upper - 1).long().unsqueeze(1), fraction.unsqueeze(1))
    return target


def _loss_sums(model, value_head, tokenizer, rows, max_length: int, device: str,
               disproof_weight: float):
    import torch
    import torch.nn.functional as functional

    batch = encode_rows(tokenizer, rows, max_length, device)
    logits, hidden = _forward_policy_and_hidden(model, batch["input_ids"], batch["attention_mask"])
    shifted_logits = logits[:, :-1, :].contiguous()
    shifted_labels = batch["labels"][:, 1:].contiguous()
    token_loss = functional.cross_entropy(
        shifted_logits.transpose(1, 2), shifted_labels, ignore_index=-100, reduction="none"
    )
    mask = shifted_labels.ne(-100)
    per_sample = (token_loss * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
    weights = torch.tensor(
        [disproof_weight if row["kind"] == "disproof" else 1.0 for row in rows],
        dtype=per_sample.dtype, device=device,
    )
    policy_sum = (per_sample * weights).sum()
    indices = batch["prompt_lengths"] - 1
    state_hidden = hidden[torch.arange(hidden.shape[0], device=device), indices].float()
    value_logits = value_head(state_hidden)
    if value_logits.shape != (len(rows), 64):
        raise ValueError(f"value head output shape must be {(len(rows), 64)}, got {tuple(value_logits.shape)}")
    distances = torch.tensor([-float(row["value_target"]) for row in rows], device=device)
    value_sum = functional.cross_entropy(value_logits, _two_hot(distances), reduction="sum")
    return policy_sum, weights.sum(), value_sum, len(rows), int(mask.sum().detach().cpu())


def _sample_replay(replay: list[dict], count: int, rng: random.Random) -> list[dict]:
    return rng.sample(replay, count) if len(replay) >= count else rng.choices(replay, k=count)


def _select_replay_batch(replay: list[dict], manifest: dict, *, count: int,
                         sampling: str, rng: random.Random) -> list[dict]:
    """Select one optimizer-step batch under the frozen replay policy.

    ``full_replay`` is intentionally fail-closed: the signed replay manifest,
    validated rows, and frozen batch size must all describe the same complete
    transition set.  This prevents a nominal one-step CE wave from silently
    degrading into a one-transition update.
    """
    manifest_count = manifest.get("stats", {}).get("transitions")
    if (isinstance(manifest_count, bool) or not isinstance(manifest_count, int)
            or manifest_count <= 0 or manifest_count != len(replay)):
        raise ValueError(
            "replay manifest transition count does not match validated rows: "
            f"manifest={manifest_count!r}, rows={len(replay)}"
        )
    if sampling == "full_replay":
        if count != len(replay):
            raise ValueError(
                "full_replay requires train.batch_size to equal the validated replay size: "
                f"batch_size={count}, replay={len(replay)}"
            )
        return list(replay)
    if sampling == "uniform_over_transitions":
        return _sample_replay(replay, count, rng)
    raise ValueError(f"unsupported replay sampling policy: {sampling!r}")


def _manifest_files(root: Path) -> list[dict]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()
                       and item.name != "checkpoint_manifest.json"):
        rows.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                     "sha256": sha256_file(path)})
    return rows


def verify_checkpoint(checkpoint: str | Path) -> dict:
    root = Path(checkpoint).resolve()
    manifest = json.loads((root / "checkpoint_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_payload_sha256") != object_hash(manifest, "manifest_payload_sha256"):
        raise ValueError("checkpoint manifest payload hash mismatch")
    declared = {entry["path"] for entry in manifest.get("files", [])}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() and path.name != "checkpoint_manifest.json"}
    if actual != declared:
        raise ValueError("checkpoint file set differs from its manifest")
    for entry in manifest.get("files", []):
        path = (root / entry["path"]).resolve()
        if root not in path.parents:
            raise ValueError("checkpoint manifest contains path traversal")
        if not path.is_file() or path.stat().st_size != int(entry["size"]):
            raise ValueError(f"checkpoint file missing/size mismatch: {entry['path']}")
        if sha256_file(path) != entry["sha256"]:
            raise ValueError(f"checkpoint file hash mismatch: {entry['path']}")
    return manifest


def _latest_checkpoint_payload(checkpoint: Path, manifest: dict) -> dict:
    return {
        "checkpoint": str(checkpoint.resolve()),
        "checkpoint_manifest_sha256": sha256_file(checkpoint / "checkpoint_manifest.json"),
        "wave_index": int(manifest["wave_index"]),
        "wave_step": int(manifest["wave_step"]),
        "global_step": int(manifest["global_step"]),
        "verified_manifest_payload_sha256": manifest["manifest_payload_sha256"],
    }


def _validate_checkpoint_progression(parent: dict, child: dict) -> None:
    if int(child["global_step"]) <= int(parent["global_step"]):
        raise ValueError("checkpoint lineage does not strictly advance global_step")
    parent_wave, child_wave = int(parent["wave_index"]), int(child["wave_index"])
    parent_step, child_step = int(parent["wave_step"]), int(child["wave_step"])
    if child_wave == parent_wave:
        if child_step <= parent_step:
            raise ValueError("checkpoint lineage does not advance wave_step")
    elif child_wave == parent_wave + 1:
        if child_step <= 0:
            raise ValueError("next-wave checkpoint has an invalid wave_step")
    else:
        raise ValueError("checkpoint lineage skips or reverses a wave")


def _current_checkpoint_state_locked(output: Path) -> tuple[str | None, list[tuple[Path, str, dict]]]:
    """Read and verify the publication head while holding the publication lock.

    The returned directory records include every complete, non-temporary checkpoint.
    Callers use them to distinguish an empty run from a crash orphan; absence of
    ``latest.json`` is not sufficient evidence that a new root may be created.
    """
    checkpoint_root = output / "checkpoints"
    records: list[tuple[Path, str, dict]] = []
    if checkpoint_root.is_dir():
        for checkpoint in sorted(
                item.resolve() for item in checkpoint_root.iterdir()
                if item.is_dir() and not item.name.startswith(".")):
            manifest = verify_checkpoint(checkpoint)
            records.append((checkpoint, sha256_file(checkpoint / "checkpoint_manifest.json"),
                            manifest))

    latest_path = output / "latest.json"
    if not latest_path.is_file():
        return None, records

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest_hash = str(latest.get("checkpoint_manifest_sha256", ""))
    matches = [record for record in records if record[1] == latest_hash]
    if len(matches) != 1:
        raise ValueError("latest.json does not name exactly one complete checkpoint")
    checkpoint, _, manifest = matches[0]
    if Path(latest.get("checkpoint", "")).resolve() != checkpoint:
        raise ValueError("latest.json checkpoint path/hash disagree")
    if latest != _latest_checkpoint_payload(checkpoint, manifest):
        raise ValueError("latest.json metadata differs from its checkpoint manifest")
    return latest_hash, records


def reconcile_checkpoints(output: str | Path,
                          requested_resume: str | Path | None = None) -> Path | None:
    """Verify one complete checkpoint chain and atomically adopt orphan successors.

    A process can die after the checkpoint-directory rename and before latest.json.
    Parent-manifest hashes make the resulting complete directory an unambiguous child.
    Temporary directories are intentionally ignored; every non-temporary checkpoint
    must belong to the single chain or reconciliation fails closed.
    """
    output_path = Path(output).resolve()
    checkpoint_root = output_path / "checkpoints"
    if not checkpoint_root.is_dir():
        if (output_path / "latest.json").exists():
            raise ValueError("latest.json exists without a checkpoint directory")
        return None

    with _checkpoint_publication_lock(output_path):
        for item in checkpoint_root.iterdir():
            if (item.is_dir() and item.name.startswith(".") and
                    ".tmp-" in item.name):
                resolved = item.resolve()
                if resolved.parent != checkpoint_root.resolve():
                    raise ValueError("checkpoint temporary path escaped its output directory")
                shutil.rmtree(resolved)
        checkpoint_dirs = sorted(
            item.resolve() for item in checkpoint_root.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        )
        if not checkpoint_dirs:
            if (output_path / "latest.json").exists():
                raise ValueError("latest.json exists without a complete checkpoint")
            return None

        records: dict[str, tuple[Path, dict]] = {}
        for checkpoint in checkpoint_dirs:
            manifest = verify_checkpoint(checkpoint)
            manifest_hash = sha256_file(checkpoint / "checkpoint_manifest.json")
            if manifest_hash in records:
                raise ValueError("duplicate checkpoint manifest hash in output")
            parent_hash = manifest.get("parent_checkpoint_manifest_sha256")
            if parent_hash is not None and (
                    not isinstance(parent_hash, str) or len(parent_hash) != 64):
                raise ValueError("checkpoint has an invalid parent manifest hash")
            records[manifest_hash] = (checkpoint, manifest)

        children: dict[str, list[str]] = {}
        roots: list[str] = []
        for manifest_hash, (_, manifest) in records.items():
            parent_hash = manifest.get("parent_checkpoint_manifest_sha256")
            if parent_hash is None:
                roots.append(manifest_hash)
            elif parent_hash not in records:
                raise ValueError("checkpoint lineage refers to a missing parent")
            else:
                children.setdefault(parent_hash, []).append(manifest_hash)
        if len(roots) != 1:
            raise ValueError("checkpoint output must contain exactly one lineage root")
        if any(len(items) != 1 for items in children.values()):
            raise ValueError("ambiguous checkpoint lineage has multiple valid successors")

        chain: list[str] = []
        current_hash = roots[0]
        while True:
            if current_hash in chain:
                raise ValueError("checkpoint lineage contains a cycle")
            chain.append(current_hash)
            next_hashes = children.get(current_hash, [])
            if not next_hashes:
                break
            next_hash = next_hashes[0]
            _validate_checkpoint_progression(records[current_hash][1], records[next_hash][1])
            current_hash = next_hash
        if len(chain) != len(records):
            raise ValueError("checkpoint output contains an unlinked checkpoint")

        latest_path = output_path / "latest.json"
        published_hash: str | None = None
        if latest_path.is_file():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            published_hash = str(latest.get("checkpoint_manifest_sha256", ""))
            if published_hash not in records:
                raise ValueError("latest.json does not name a checkpoint in the verified chain")
            published_checkpoint, published_manifest = records[published_hash]
            if Path(latest.get("checkpoint", "")).resolve() != published_checkpoint:
                raise ValueError("latest.json checkpoint path/hash disagree")
            if latest != _latest_checkpoint_payload(published_checkpoint, published_manifest):
                raise ValueError("latest.json metadata differs from its checkpoint manifest")

        if requested_resume is not None:
            requested = Path(requested_resume).resolve()
            if requested not in {record[0] for record in records.values()}:
                raise ValueError("requested resume checkpoint is outside the verified lineage")
            # A stale, formerly published resume is safe: the child is bound to it
            # by parent hash and will be adopted below.
            if published_hash is not None:
                published_index = chain.index(published_hash)
                requested_hash = next(key for key, value in records.items() if value[0] == requested)
                if chain.index(requested_hash) > published_index:
                    raise ValueError("requested resume was never atomically published")

        terminal_hash = chain[-1]
        terminal_checkpoint, terminal_manifest = records[terminal_hash]
        terminal_payload = _latest_checkpoint_payload(terminal_checkpoint, terminal_manifest)
        if not latest_path.is_file() or published_hash != terminal_hash:
            _atomic_json(latest_path, terminal_payload)
        return terminal_checkpoint


def write_checkpoint(model, value_head, optimizer, output: Path, *, wave_index: int,
                     wave_step: int, global_step: int, rng: random.Random,
                     metadata: dict, receipt: dict,
                     parent_checkpoint_manifest_sha256: str | None = None) -> Path:
    output = Path(output).resolve()
    with _checkpoint_publication_lock(output):
        return _write_checkpoint_locked(
            model, value_head, optimizer, output, wave_index=wave_index,
            wave_step=wave_step, global_step=global_step, rng=rng,
            metadata=metadata, receipt=receipt,
            parent_checkpoint_manifest_sha256=parent_checkpoint_manifest_sha256,
        )


def _write_checkpoint_locked(model, value_head, optimizer, output: Path, *, wave_index: int,
                             wave_step: int, global_step: int, rng: random.Random,
                             metadata: dict, receipt: dict,
                             parent_checkpoint_manifest_sha256: str | None = None) -> Path:
    import torch

    name = f"wave_{wave_index:03d}_step_{wave_step:06d}_global_{global_step:08d}"
    final = output / "checkpoints" / name
    final.parent.mkdir(parents=True, exist_ok=True)
    current_hash, complete_records = _current_checkpoint_state_locked(output)
    if parent_checkpoint_manifest_sha256 is not None:
        try:
            valid_parent_hash = (len(parent_checkpoint_manifest_sha256) == 64 and
                                 bytes.fromhex(parent_checkpoint_manifest_sha256) is not None)
        except (TypeError, ValueError):
            valid_parent_hash = False
        if not valid_parent_hash:
            raise ValueError("parent checkpoint manifest hash must be 64 hexadecimal characters")
    if final.exists():
        # Idempotent crash recovery: never overwrite, only adopt an exact,
        # already-complete checkpoint for the requested logical step.
        existing = verify_checkpoint(final)
        expected = (wave_index, wave_step, global_step,
                    parent_checkpoint_manifest_sha256)
        actual = (int(existing.get("wave_index", -1)),
                  int(existing.get("wave_step", -1)),
                  int(existing.get("global_step", -1)),
                  existing.get("parent_checkpoint_manifest_sha256"))
        if actual != expected:
            raise FileExistsError(f"checkpoint slot exists with different lineage: {final}")
        existing_hash = sha256_file(final / "checkpoint_manifest.json")
        if current_hash == existing_hash:
            return final
        if current_hash is None:
            if not (parent_checkpoint_manifest_sha256 is None and
                    len(complete_records) == 1 and complete_records[0][0] == final):
                raise ValueError(
                    "cannot adopt checkpoint without latest.json: output is not a single root orphan"
                )
        elif current_hash != parent_checkpoint_manifest_sha256:
            raise ValueError(
                "stale checkpoint parent: latest manifest hash changed before publication"
            )
        sibling_successors = [
            path for path, manifest_hash, manifest in complete_records
            if manifest_hash != existing_hash and
            manifest.get("parent_checkpoint_manifest_sha256") == current_hash
        ]
        if sibling_successors:
            raise ValueError("cannot adopt checkpoint beside another complete successor")
        _atomic_json(output / "latest.json", _latest_checkpoint_payload(final, existing))
        return final

    # Strict compare-and-swap: a new root is legal only for a genuinely empty
    # run, while every successor must name the currently published head.
    if current_hash is None:
        if complete_records:
            raise ValueError(
                "complete checkpoint exists without latest.json; reconcile or retry that checkpoint"
            )
        if parent_checkpoint_manifest_sha256 is not None:
            raise ValueError("checkpoint parent supplied but no latest checkpoint is published")
    elif parent_checkpoint_manifest_sha256 != current_hash:
        raise ValueError("stale checkpoint parent: latest manifest hash changed before publication")

    orphan_successors = [
        path for path, _, manifest in complete_records
        if manifest.get("parent_checkpoint_manifest_sha256") == current_hash
    ]
    if orphan_successors:
        raise ValueError("complete successor exists beyond latest.json; reconcile before publishing")

    temporary = final.parent / f".{name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    model.save_pretrained(temporary / "adapter")
    torch.save(value_head.state_dict(), temporary / "value_head.pt")
    torch.save(
        {
            "optimizer": optimizer.state_dict(),
            "python_global_rng": random.getstate(),
            "python_sampler_rng": rng.getstate(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            "wave_index": wave_index,
            "wave_step": wave_step,
            "global_step": global_step,
            "metadata": metadata,
        },
        temporary / "trainer_state.pt",
    )
    _atomic_json(temporary / "metadata.json", metadata | {
        "wave_index": wave_index, "wave_step": wave_step, "global_step": global_step
    })
    _atomic_json(temporary / "receipt.json", receipt)
    if os.name != "nt":
        for path in (item for item in temporary.rglob("*") if item.is_file()):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    manifest = {
        "schema_version": 2,
        "status": "complete",
        "wave_index": wave_index,
        "wave_step": wave_step,
        "global_step": global_step,
        "parent_checkpoint_manifest_sha256": parent_checkpoint_manifest_sha256,
        "files": _manifest_files(temporary),
    }
    manifest["manifest_payload_sha256"] = object_hash(manifest)
    _atomic_json(temporary / "checkpoint_manifest.json", manifest)
    _fsync_directory(temporary)
    if final.exists():
        raise FileExistsError(f"refusing to replace concurrently published checkpoint: {final}")
    os.replace(temporary, final)
    _fsync_directory(final.parent)
    verified = verify_checkpoint(final)
    _atomic_json(output / "latest.json", _latest_checkpoint_payload(final, verified))
    return final


def _seed_all(seed: int, torch, deterministic_algorithms: bool) -> tuple[random.Random, dict]:
    random.seed(seed)
    rng = random.Random(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(deterministic_algorithms, warn_only=False)
    return rng, {
        "seed": seed,
        "torch_deterministic_algorithms": bool(deterministic_algorithms),
        "cudnn_benchmark": bool(getattr(torch.backends.cudnn, "benchmark", False)),
        "cudnn_deterministic": bool(getattr(torch.backends.cudnn, "deterministic", False)),
        "cuda_seeded": bool(torch.cuda.is_available()),
        "hip_version": getattr(torch.version, "hip", None),
    }


def _validate_value_head_shapes(value_head, hidden_size: int) -> None:
    expected = {
        "net.0.weight": (256, hidden_size), "net.0.bias": (256,),
        "net.2.weight": (64, 256), "net.2.bias": (64,),
    }
    actual = {key: tuple(value.shape) for key, value in value_head.state_dict().items()}
    if actual != expected:
        raise ValueError(f"value head shape mismatch: expected {expected}, got {actual}")


def _validate_lora_coverage(model, targets: list[str]) -> dict[str, int]:
    names = [name for name, _ in model.named_modules()]
    coverage = {target: sum(name.endswith(target) for name in names) for target in targets}
    missing = [target for target, count in coverage.items() if count == 0]
    if missing:
        raise ValueError(f"LoRA target modules absent from the base model: {missing}")
    if not any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError("LoRA injection produced no trainable policy parameters")
    return coverage


def run(config_path: str | Path, replay_bundle: str | Path, output_dir: str | Path, *,
        wave_index: int, steps_override: int | None = None, allow_draft: bool = False,
        resume_checkpoint: str | Path | None = None,
        expected_config_sha256: str | None = None) -> dict:
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_train_config(config, allow_draft=allow_draft)
    config_hash = sha256_file(config_path)
    if expected_config_sha256 is None and not allow_draft:
        raise ValueError("formal training requires --expected-config-sha256 from the run manifest")
    if expected_config_sha256 is not None and config_hash != expected_config_sha256:
        raise ValueError("frozen config hash differs from --expected-config-sha256")
    verify_declared_locks(config)
    dataset = config.get("dataset", {})
    train_variants = dataset.get("train_variants", dataset.get("adaptation_variants"))
    if (not isinstance(train_variants, list)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in train_variants)
            or wave_index not in train_variants):
        raise ValueError("wave_index is outside the frozen dataset training variants")
    train = config["train"]
    steps = int(steps_override if steps_override is not None else train["steps_per_wave"])
    if steps <= 0:
        raise ValueError("requested wave steps must be positive")
    if not allow_draft and steps_override is not None and steps != int(train["steps_per_wave"]):
        raise ValueError("formal run cannot override frozen steps_per_wave")

    output = Path(output_dir).resolve()
    resume = Path(resume_checkpoint).resolve() if resume_checkpoint else None
    if ((output / "latest.json").exists() or
            ((output / "checkpoints").is_dir() and any(
                item.is_dir() for item in (output / "checkpoints").iterdir()
            ))):
        recovered = reconcile_checkpoints(output, requested_resume=resume)
        if recovered is not None:
            resume = recovered
    if resume is None:
        if not allow_draft and wave_index != 1:
            raise ValueError("a formal fresh run must begin at wave 1")
        if output.exists():
            material = [item for item in output.iterdir() if item.name != "checkpoints"]
            checkpoint_material = []
            if (output / "checkpoints").is_dir():
                checkpoint_material = [
                    item for item in (output / "checkpoints").iterdir()
                    if item.name != ".publication.lock"
                ]
            if material or checkpoint_material:
                raise FileExistsError("fresh run requires a new or empty output directory")
        output.mkdir(parents=True, exist_ok=True)
    else:
        if not output.is_dir() or not (output / "latest.json").is_file():
            raise ValueError("resume requires an existing run with latest.json")
        latest = json.loads((output / "latest.json").read_text(encoding="utf-8"))
        if Path(latest["checkpoint"]).resolve() != resume:
            raise ValueError("resume checkpoint is not the atomically published latest checkpoint")
        if latest["checkpoint_manifest_sha256"] != sha256_file(resume / "checkpoint_manifest.json"):
            raise ValueError("latest checkpoint manifest hash mismatch")
        verify_checkpoint(resume)

    transitions_path, replay_manifest, replay_manifest_hash = validate_replay_bundle(
        replay_bundle, config, wave_index, config_hash
    )
    asset_lock_entry = config["locks"]["asset_lock"]

    import torch
    import cryptography
    import peft
    import transformers
    runtime_versions = {"torch": torch.__version__, "transformers": transformers.__version__,
                        "peft": peft.__version__, "cryptography": cryptography.__version__}
    asset_lock = verify_asset_lock(asset_lock_entry["path"], asset_lock_entry["sha256"],
                                   runtime_versions=runtime_versions)
    initial_adapter_root, initial_adapter_manifest = verify_initial_adapter(config, asset_lock)
    role_paths = {entry["role"]: str(Path(entry["path"]).resolve())
                  for entry in asset_lock["files"] if entry["role"] != "model_shard"}
    if Path(asset_lock["model_root"]).resolve() != Path(config["model"]["base_path"]).resolve():
        raise ValueError("configured base model path is not the asset-locked model directory")
    if asset_lock["model_revision"] != config["model"]["base_revision"]:
        raise ValueError("configured base model revision differs from the asset lock")
    if Path(role_paths["value_head"]) != Path(config["model"]["value_head_path"]).resolve():
        raise ValueError("configured value head path is not the asset-locked value head")
    if Path(asset_lock["target_git"]["path"]).resolve() != Path(
        config["source_pin"]["target_repo_root"]
    ).resolve():
        raise ValueError("configured target repository is not the asset-locked checkout")
    seed = int(train["seed"])
    rng, seed_receipt = _seed_all(seed, torch, bool(train.get("deterministic_algorithms", True)))

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    target_repo = Path(config["source_pin"]["target_repo_root"])
    sys.path.insert(0, str(target_repo))
    from alphaproof.net.value_head import ValueHead64, load_s18_head

    model_path = config["model"]["base_path"]
    device = str(train["device"])
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("configured CUDA/ROCm device is unavailable")
    dtype = {"bfloat16": torch.bfloat16, "float32": torch.float32}.get(train["dtype"])
    if dtype is None:
        raise ValueError("dtype must be bfloat16 or float32")
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    try:
        vocabulary_size = int(len(tokenizer))
    except TypeError:
        vocabulary_size = int(tokenizer.vocab_size)
    # Re-read through the frozen-hash loader at the point of use.  This closes
    # the validate→train replacement window without trusting a mutable path.
    trusted_verifier_lock = _load_frozen_verifier_lock(config)
    rows = load_transition_rows(
        transitions_path,
        vocabulary_size=vocabulary_size,
        actor_config_sha256=_frozen_receipt_identity(config, "shared_actor_config"),
        budget_config_sha256=config["locks"]["shared_budget_config"]["sha256"],
        tokenizer_lock_sha256=_frozen_receipt_identity(config, "tokenizer_lock"),
        max_wave=wave_index,
        trusted_verifier_id=config["locks"]["trusted_verifier_lock"]["verifier_id"],
        trusted_verifier_lock_sha256=config["locks"]["trusted_verifier_lock"]["sha256"],
        trusted_verifier_public_key_hex=trusted_verifier_lock["ed25519_public_key_hex"],
    )
    validate_all_tokenization(tokenizer, rows)
    model = AutoModelForCausalLM.from_pretrained(model_path, local_files_only=True, torch_dtype=dtype)
    lora = train["lora"]
    if resume is not None:
        model = PeftModel.from_pretrained(model, resume / "adapter", is_trainable=True)
    else:
        model = PeftModel.from_pretrained(
            model, initial_adapter_root, is_trainable=True, adapter_name="default"
        )
        model.set_adapter("default")
        observed_initial_state = _formal_trainable_state_sha256(model, torch)
        expected_initial_state = initial_adapter_manifest["trainable_state_sha256"]
        if observed_initial_state != expected_initial_state:
            raise ValueError(
                "loaded initial adapter trainable-state hash mismatch: "
                f"expected {expected_initial_state}, got {observed_initial_state}"
            )
    lora_coverage = _validate_lora_coverage(model, list(lora["target_modules"]))
    model = model.to(device)
    model.config.use_cache = False
    if train.get("gradient_checkpointing", True):
        model.gradient_checkpointing_enable()
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()

    hidden_size = int(model.config.hidden_size)
    if hidden_size != int(config["model"]["hidden_size"]):
        raise ValueError("base model hidden size differs from frozen config")
    value_head = ValueHead64(hidden_size=hidden_size, mid=256, bins=64).to(device)
    if resume is not None:
        value_head.load_state_dict(torch.load(resume / "value_head.pt", map_location=device,
                                              weights_only=True), strict=True)
    else:
        report = load_s18_head(value_head, config["model"]["value_head_path"])
        if report["missing"] or report["unexpected"]:
            raise ValueError(f"value-head keys mismatch: {report}")
    _validate_value_head_shapes(value_head, hidden_size)

    policy_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(
        [{"params": policy_parameters, "lr": float(train["policy_lr"])},
         {"params": list(value_head.parameters()), "lr": float(train["value_lr"])}],
        weight_decay=float(train.get("weight_decay", 0.0)),
    )
    wave_start_step = 0
    global_step = 0
    total_learner_tokens = 0
    if resume is not None:
        state = torch.load(resume / "trainer_state.pt", map_location="cpu", weights_only=False)
        prior = state["metadata"]
        required_prior = {
            "config_sha256": config_hash,
            "asset_lock_sha256": asset_lock_entry["sha256"],
            "initial_adapter_manifest_payload_sha256": (
                initial_adapter_manifest["manifest_payload_sha256"]
            ),
            "actor_config_file_sha256": config["locks"]["shared_actor_config"]["sha256"],
            "actor_config_sha256": _frozen_receipt_identity(
                config, "shared_actor_config"
            ),
            "budget_config_sha256": config["locks"]["shared_budget_config"]["sha256"],
            "tokenizer_lock_file_sha256": config["locks"]["tokenizer_lock"]["sha256"],
            "tokenizer_lock_sha256": _frozen_receipt_identity(config, "tokenizer_lock"),
        }
        for key, expected in required_prior.items():
            if prior.get(key) != expected:
                raise ValueError(f"resume checkpoint provenance mismatch for {key}")
        prior_wave = int(state["wave_index"])
        if prior_wave == wave_index:
            if prior.get("replay_manifest_sha256") != replay_manifest_hash:
                raise ValueError("same-wave resume replay differs from checkpoint")
            wave_start_step = int(state["wave_step"])
        elif prior_wave + 1 == wave_index:
            if int(state["wave_step"]) != int(train["steps_per_wave"]):
                raise ValueError("next wave can start only from a completed prior wave")
            wave_start_step = 0
        else:
            raise ValueError("resume wave must equal checkpoint wave or its immediate successor")
        if steps < wave_start_step:
            raise ValueError("requested wave steps precede the recovered checkpoint")
        optimizer.load_state_dict(state["optimizer"])
        random.setstate(state["python_global_rng"])
        rng.setstate(state["python_sampler_rng"])
        torch.set_rng_state(state["torch_rng"])
        if torch.cuda.is_available() and state.get("cuda_rng") is not None:
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        global_step = int(state["global_step"])
        total_learner_tokens = int(prior.get("learner_action_tokens", 0))

    metadata_base = {
        "schema_version": 2,
        "arm": "official_search_path_ce",
        "config_path": str(config_path),
        "config_sha256": config_hash,
        "asset_lock_sha256": asset_lock_entry["sha256"],
        "asset_lock_payload_sha256": object_hash(asset_lock),
        "initial_adapter_manifest_payload_sha256": (
            initial_adapter_manifest["manifest_payload_sha256"]
        ),
        "initial_adapter_trainable_state_sha256": (
            initial_adapter_manifest["trainable_state_sha256"]
        ),
        "replay_manifest_sha256": replay_manifest_hash,
        "replay_transition_sha256": replay_manifest["transition_sha256"],
        "course_manifest_sha256": replay_manifest["course_manifest_sha256"],
        "actor_config_file_sha256": config["locks"]["shared_actor_config"]["sha256"],
        "actor_config_sha256": replay_manifest["actor_config_sha256"],
        "budget_config_sha256": replay_manifest["budget_config_sha256"],
        "tokenizer_lock_file_sha256": config["locks"]["tokenizer_lock"]["sha256"],
        "tokenizer_lock_sha256": replay_manifest["tokenizer_lock_sha256"],
        "trusted_verifier_lock_sha256": replay_manifest["trusted_verifier_lock_sha256"],
        "base_model_revision": config["model"]["base_revision"],
        "runtime_versions": runtime_versions,
        "seed_receipt": seed_receipt,
        "lora_coverage": lora_coverage,
        "sft_mix": 0.0,
    }
    checkpoint_every = int(train["checkpoint_every_steps"])
    started = time.time()
    last_checkpoint = resume
    parent_checkpoint_manifest_sha256 = (
        sha256_file(resume / "checkpoint_manifest.json") if resume is not None else None
    )
    model.train()
    value_head.train()
    for wave_step in range(wave_start_step + 1, steps + 1):
        selected = _select_replay_batch(
            rows, replay_manifest, count=int(train["batch_size"]),
            sampling=str(config["replay"]["sampling"]), rng=rng,
        )
        selected_ids = [row["extra"]["transition_id"] for row in selected]
        selection_sha256 = object_hash(selected_ids)
        policy_weight_total = sum(
            float(train.get("disproof_weight", 1.0)) if row["kind"] == "disproof" else 1.0
            for row in selected
        )
        optimizer.zero_grad(set_to_none=True)
        policy_value = value_value = 0.0
        step_tokens = 0
        micro_size = int(train["micro_batch_size"])
        for offset in range(0, len(selected), micro_size):
            micro = selected[offset:offset + micro_size]
            print(json.dumps({"event": "ce_micro_start", "wave": wave_index,
                              "wave_step": wave_step, "micro": offset // micro_size + 1,
                              "micro_total": len(selected) // micro_size}), flush=True)
            policy_sum, _, value_sum, _, tokens = _loss_sums(
                model, value_head, tokenizer, micro, int(train["max_length"]), device,
                float(train.get("disproof_weight", 1.0)),
            )
            loss = (policy_sum / policy_weight_total +
                    float(train["value_coef"]) * value_sum / len(selected))
            if not bool(torch.isfinite(loss).item()):
                raise FloatingPointError(f"non-finite loss at wave {wave_index} step {wave_step}")
            loss.backward()
            policy_value += float(policy_sum.detach().cpu())
            value_value += float(value_sum.detach().cpu())
            step_tokens += tokens
        parameters = policy_parameters + list(value_head.parameters())
        grad_norm = torch.nn.utils.clip_grad_norm_(parameters, float(train["max_grad_norm"]))
        if not bool(torch.isfinite(torch.as_tensor(grad_norm)).item()):
            raise FloatingPointError(f"non-finite grad norm at wave {wave_index} step {wave_step}")
        optimizer.step()
        global_step += 1
        total_learner_tokens += step_tokens
        receipt = {
            "wave_index": wave_index,
            "wave_step": wave_step,
            "global_step": global_step,
            "selection_sha256": selection_sha256,
            "selected_transition_ids": selected_ids,
            "policy_loss": policy_value / policy_weight_total,
            "value_loss": value_value / len(selected),
            "loss": policy_value / policy_weight_total + float(train["value_coef"]) * value_value / len(selected),
            "grad_norm": float(torch.as_tensor(grad_norm).detach().cpu()),
            "samples": len(selected),
            "learner_action_tokens": step_tokens,
            "cumulative_learner_action_tokens": total_learner_tokens,
            "elapsed_seconds": round(time.time() - started, 3),
            "replay_manifest_sha256": replay_manifest_hash,
        }
        metadata = metadata_base | {"learner_action_tokens": total_learner_tokens}
        if wave_step % checkpoint_every == 0 or wave_step == steps:
            last_checkpoint = write_checkpoint(
                model, value_head, optimizer, output, wave_index=wave_index,
                wave_step=wave_step, global_step=global_step, rng=rng,
                metadata=metadata, receipt=receipt,
                parent_checkpoint_manifest_sha256=parent_checkpoint_manifest_sha256,
            )
            parent_checkpoint_manifest_sha256 = sha256_file(
                last_checkpoint / "checkpoint_manifest.json"
            )
        print(json.dumps({"event": "ce_step_complete"} | receipt), flush=True)

    if last_checkpoint is None:
        raise AssertionError("no checkpoint was published")
    verified = verify_checkpoint(last_checkpoint)
    done = metadata_base | {
        "wave_index": wave_index,
        "wave_steps": steps,
        "global_step": global_step,
        "checkpoint": str(last_checkpoint.resolve()),
        "checkpoint_manifest_sha256": sha256_file(last_checkpoint / "checkpoint_manifest.json"),
        "verified_manifest_payload_sha256": verified["manifest_payload_sha256"],
        "learner_action_tokens": total_learner_tokens,
    }
    _atomic_json(output / f"DONE.wave_{wave_index:03d}.json", done)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--replay", required=True, help="complete replay bundle directory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--wave-index", required=True, type=int)
    parser.add_argument("--steps", type=int, help="desired total steps within this wave")
    parser.add_argument("--resume", help="latest verified checkpoint from this or prior wave")
    parser.add_argument("--expected-config-sha256",
                        help="config hash pinned by the external immutable run manifest")
    parser.add_argument("--allow-draft", action="store_true", help="smoke only")
    args = parser.parse_args(argv)
    result = run(
        args.config, args.replay, args.output, wave_index=args.wave_index,
        steps_override=args.steps, allow_draft=args.allow_draft,
        resume_checkpoint=args.resume, expected_config_sha256=args.expected_config_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

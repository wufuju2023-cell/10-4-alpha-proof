"""Generate immutable asset and tokenizer locks for the formal CE run."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from .replay import object_hash, sha256_file


def _entry(path: Path, role: str) -> dict:
    path = path.resolve()
    return {"role": role, "path": str(path), "size": path.stat().st_size,
            "sha256": sha256_file(path)}


def _write_new(path: Path, value: dict) -> None:
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite lock: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def generate_tokenizer_lock(model_root: str | Path, model_revision: str,
                            output: str | Path) -> dict:
    root = Path(model_root).resolve()
    names = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
             "added_tokens.json", "vocab.json", "merges.txt")
    paths = [root / name for name in names if (root / name).is_file()]
    files = [_entry(path, "tokenizer") for path in paths]
    present = {Path(entry["path"]).name for entry in files}
    if not {"tokenizer.json", "tokenizer_config.json"}.issubset(present):
        raise ValueError("model snapshot lacks tokenizer.json/tokenizer_config.json")
    identity_manifest = [
        {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in paths
    ]
    lock = {
        "schema_version": 1,
        "model_root": str(root),
        "model_revision": model_revision,
        "files": files,
        "receipt_identity_manifest": identity_manifest,
        "receipt_identity_sha256": object_hash(identity_manifest),
    }
    _write_new(Path(output), lock)
    return lock


def generate_asset_lock(model_root: str | Path, model_revision: str,
                        value_head: str | Path, initial_adapter: str | Path,
                        target_repo: str | Path,
                        target_value_head_source: str | Path, output: str | Path) -> dict:
    root = Path(model_root).resolve()
    adapter_root = Path(initial_adapter).resolve()
    repo = Path(target_repo).resolve()
    index_path = root / "model.safetensors.index.json"
    if not index_path.is_file():
        raise ValueError("model.safetensors.index.json is required")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    shard_names = set(index.get("weight_map", {}).values())
    if not shard_names:
        raise ValueError("model index has no weight_map shards")
    role_by_name = {
        "config.json": "model_config",
        "generation_config.json": "generation_config",
        "tokenizer_config.json": "tokenizer_config",
        "tokenizer.json": "tokenizer",
        "model.safetensors.index.json": "model_index",
    }
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in shard_names:
            role = "model_shard"
        else:
            role = role_by_name.get(path.name, "model_aux")
        files.append(_entry(path, role))
    files.append(_entry(Path(value_head), "value_head"))
    files.append(_entry(Path(target_value_head_source), "target_value_head_source"))
    if not adapter_root.is_dir():
        raise ValueError("initial adapter directory is missing")
    adapter_roles = {
        "adapter_config.json": "initial_adapter_config",
        "adapter_model.safetensors": "initial_adapter_model",
        "formal_initial_adapter_manifest.json": "initial_adapter_manifest",
    }
    for path in sorted(item for item in adapter_root.rglob("*") if item.is_file()):
        files.append(_entry(path, adapter_roles.get(path.name, "initial_adapter_aux")))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    if dirty:
        raise ValueError("target repository must be clean before generating the asset lock")
    import cryptography
    import peft
    import torch
    import transformers
    runtime = {"torch": torch.__version__, "transformers": transformers.__version__,
               "peft": peft.__version__, "cryptography": cryptography.__version__}
    lock = {
        "schema_version": 1,
        "model_root": str(root),
        "model_revision": model_revision,
        "initial_adapter_root": str(adapter_root),
        "files": files,
        "target_git": {"path": str(repo), "commit": commit},
        "runtime_versions": runtime,
    }
    _write_new(Path(output), lock)
    return lock

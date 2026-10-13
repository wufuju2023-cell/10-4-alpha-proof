"""Pinned REAL-Prover/PEFT entrypoint for one Online-v2 update.

The join workstream writes immutable JSON representations of the reviewed
``SearchStateReceipt``, ``VerifierReceipt`` and ``ProofPathReceipt`` classes.
This module is the deliberately small runtime adapter from those files to the
existing :class:`OnlineV2Learner`; it does not implement another objective.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, ContextManager, Iterable, Iterator, Mapping, Sequence

import torch

from .adapter_reference import (
    AdapterReferenceManifest,
    ArtifactIdentity,
    SingleBackboneAdapterReferences,
)
from .builder import ValidatedRolloutWave, build_rollout_samples
from .learner import OnlineV2Config, OnlineV2Learner
from .receipts import CandidateReceipt, ProofPathReceipt, SearchStateReceipt, VerifierReceipt


CONFIG_SCHEMA = "fate.online_v2.real_one_update.v1"
JOIN_SCHEMA = "fate.online_v2.converted_receipts.v1"
DONE_SCHEMA = "fate.online_v2.real_one_update.done.v1"
HEX = frozenset("0123456789abcdef")


class RealRunnerError(RuntimeError):
    """The pinned real-model update cannot safely proceed."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in HEX for c in value.lower()):
        raise RealRunnerError(f"{name} must be a 64-character SHA-256")
    return value.lower()


def require_behavior_adapter_name(
    behavior_adapter_name: str, identity: Mapping[str, Any]
) -> str:
    """Require the reload alias bound into a producer-side state digest."""

    expected = identity.get("behavior_version")
    if not isinstance(expected, str) or not expected:
        raise RealRunnerError("joined receipt behavior_version is missing")
    if behavior_adapter_name != expected:
        raise RealRunnerError(
            "behavior adapter name must equal the joined receipt behavior_version "
            "because the behavior state hash binds PEFT parameter names: "
            f"expected {expected!r}, got {behavior_adapter_name!r}"
        )
    return behavior_adapter_name


def _load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealRunnerError(f"unreadable JSON: {source}") from exc


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _candidate_from_json(value: Mapping[str, Any]) -> CandidateReceipt:
    payload = dict(value)
    for key in (
        "input_ids", "attention_mask", "action_mask", "old_logprobs",
        "execution_action_token_ids", "raw_sample_indices", "tactic_token_spans",
    ):
        if key in payload and payload[key] is not None:
            if key == "tactic_token_spans":
                payload[key] = tuple(tuple(span) for span in payload[key])
            else:
                payload[key] = tuple(payload[key])
    if payload.get("unwarped_old_logprobs") is not None:
        payload["unwarped_old_logprobs"] = tuple(payload["unwarped_old_logprobs"])
    return CandidateReceipt(**payload)


def _search_from_json(value: Mapping[str, Any]) -> SearchStateReceipt:
    payload = dict(value)
    payload["prompt_token_ids"] = tuple(payload["prompt_token_ids"])
    payload["candidates"] = tuple(_candidate_from_json(item) for item in payload["candidates"])
    return SearchStateReceipt(**payload)


def _path_from_json(value: Mapping[str, Any]) -> ProofPathReceipt:
    payload = dict(value)
    payload["event_ids"] = tuple(payload["event_ids"])
    return ProofPathReceipt(**payload)


def load_join_receipts(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[ValidatedRolloutWave, tuple[dict[str, str], ...]]:
    """Load and fully revalidate one or more exact join-produced JSON files."""

    searches: list[SearchStateReceipt] = []
    verifiers: list[VerifierReceipt] = []
    paths: list[ProofPathReceipt] = []
    evidence: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        path = Path(str(entry.get("path", ""))).resolve()
        if not path.is_file():
            raise RealRunnerError(f"receipt file is missing: {path}")
        expected = _require_sha(entry.get("sha256"), f"receipts[{index}].sha256")
        actual = sha256_file(path)
        if actual != expected:
            raise RealRunnerError(f"receipt file hash mismatch: {path}")
        payload = _load_json(path)
        if not isinstance(payload, dict) or payload.get("schema_version") != JOIN_SCHEMA:
            raise RealRunnerError(f"wrong join receipt schema: {path}")
        if set(payload) != {
            "schema_version", "source_receipt_sha256", "searches", "verifiers", "paths"
        }:
            raise RealRunnerError(f"join receipt has missing or unknown top-level fields: {path}")
        source_expected = _require_sha(
            entry.get("source_receipt_sha256"),
            f"receipts[{index}].source_receipt_sha256",
        )
        if payload.get("source_receipt_sha256") != source_expected:
            raise RealRunnerError(f"source signed-receipt hash mismatch: {path}")
        source_path = Path(str(entry.get("source_receipt_path", ""))).resolve()
        source_file_expected = _require_sha(
            entry.get("source_receipt_file_sha256"),
            f"receipts[{index}].source_receipt_file_sha256",
        )
        if not source_path.is_file() or sha256_file(source_path) != source_file_expected:
            raise RealRunnerError(f"signed source receipt file differs from its pin: {source_path}")
        source_payload = _load_json(source_path)
        if not isinstance(source_payload, dict) or source_payload.get("receipt_sha256") != source_expected:
            raise RealRunnerError("signed source file does not carry the joined source payload hash")
        try:
            searches.extend(_search_from_json(item) for item in payload["searches"])
            verifiers.extend(VerifierReceipt(**item) for item in payload["verifiers"])
            paths.extend(_path_from_json(item) for item in payload["paths"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RealRunnerError(f"malformed join receipt: {path}") from exc
        evidence.append({
            "path": str(path), "sha256": actual,
            "source_receipt_sha256": source_expected,
            "source_receipt_path": str(source_path),
            "source_receipt_file_sha256": source_file_expected,
        })
    if not evidence:
        raise RealRunnerError("at least one pinned join receipt is required")
    try:
        wave = build_rollout_samples(searches, verifiers, paths)
    except (TypeError, ValueError) as exc:
        raise RealRunnerError("joined receipts do not form one valid Online-v2 wave") from exc
    return wave, tuple(evidence)


def load_frozen_config(path: str | Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    config_path = Path(path).resolve()
    expected = _require_sha(expected_sha256, "expected config SHA-256")
    actual = sha256_file(config_path)
    if actual != expected:
        raise RealRunnerError(f"config hash mismatch: expected {expected}, got {actual}")
    config = _load_json(config_path)
    if not isinstance(config, dict) or config.get("schema_version") != CONFIG_SCHEMA:
        raise RealRunnerError("unsupported real one-update config schema")
    if config.get("status") != "frozen":
        raise RealRunnerError("real one-update config must have status='frozen'")
    if config.get("mode") not in {"smoke", "formal"}:
        raise RealRunnerError("config mode must be smoke or formal")
    return config, actual


def _verify_file_pin(value: Mapping[str, Any], label: str) -> Path:
    path = Path(str(value.get("path", ""))).resolve()
    expected = _require_sha(value.get("sha256"), f"{label}.sha256")
    if not path.is_file() or sha256_file(path) != expected:
        raise RealRunnerError(f"{label} is missing or differs from its frozen hash: {path}")
    return path


def _verify_runtime_versions(expected: Mapping[str, Any]) -> dict[str, str]:
    import peft
    import transformers

    actual = {
        "python": ".".join(map(str, sys.version_info[:3])),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "peft": peft.__version__,
    }
    if not {"torch", "transformers", "peft"}.issubset(expected):
        raise RealRunnerError("runtime_versions must pin torch, transformers and peft")
    for name, value in expected.items():
        if name not in actual or actual[name] != value:
            raise RealRunnerError(
                f"runtime version mismatch for {name}: expected {value}, got {actual.get(name)}"
            )
    return actual


def validate_pins(
    config: Mapping[str, Any], wave: ValidatedRolloutWave,
) -> dict[str, Any]:
    """Verify all inexpensive pins before allocating the 7B model."""

    pins = config.get("pins")
    train = config.get("train")
    if not isinstance(pins, Mapping) or not isinstance(train, Mapping):
        raise RealRunnerError("config requires pins and train objects")
    mode = config["mode"]
    required_train = {
        "seed", "device", "dtype", "learning_rate", "value_learning_rate",
        "weight_decay", "micro_batch_size", "update_epochs",
        "min_independent_problems", "clip_epsilon", "behavior_kl_beta",
        "anchor_kl_beta", "target_behavior_kl", "hard_behavior_kl_limit",
        "hard_anchor_kl_limit", "early_stop_kl_multiplier", "entropy_coef",
        "value_coef", "max_grad_norm", "old_logprob_tolerance", "pad_token_id",
        "gradient_checkpointing",
    }
    missing_train = sorted(required_train - set(train))
    if missing_train:
        raise RealRunnerError(f"unfrozen train fields: {missing_train}")
    independent = len({sample.problem_id for sample in wave})
    minimum = int(train.get("min_independent_problems", 0))
    if minimum < 1:
        raise RealRunnerError("min_independent_problems must be positive")
    if mode == "smoke" and (minimum != 1 or int(train["update_epochs"]) != 1):
        raise RealRunnerError("smoke mode requires one problem and exactly one update epoch")
    if mode == "formal" and (minimum != 20 or int(train["update_epochs"]) != 2):
        raise RealRunnerError("formal mode requires 20 problems and two update epochs")
    if independent < minimum:
        raise RealRunnerError(f"wave has {independent} independent problems; config requires {minimum}")
    if int(train.get("update_epochs", 0)) not in (1, 2):
        raise RealRunnerError("update_epochs must be 1 or 2")
    if int(train.get("micro_batch_size", 0)) < 1:
        raise RealRunnerError("micro_batch_size must be positive")
    for field in ("learning_rate", "value_learning_rate"):
        value = train.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise RealRunnerError(f"train.{field} must be positive")

    model = pins.get("model")
    adapter = pins.get("adapter")
    value = pins.get("value_head")
    target = pins.get("target_repo")
    if not all(isinstance(item, Mapping) for item in (model, adapter, value, target)):
        raise RealRunnerError("model/adapter/value_head/target_repo pin objects are required")
    model_root = Path(str(model.get("path", ""))).resolve()
    adapter_root = Path(str(adapter.get("path", ""))).resolve()
    target_root = Path(str(target.get("path", ""))).resolve()
    if not model_root.is_dir() or not adapter_root.is_dir() or not target_root.is_dir():
        raise RealRunnerError("a frozen model/adapter/target-repo directory is missing")
    model_lock = _verify_file_pin({
        "path": model_root / "reap-model-lock.json", "sha256": model.get("model_lock_sha256")
    }, "model lock")
    lock = _load_json(model_lock)
    base_sha = _require_sha(model.get("base_sha256"), "model.base_sha256")
    if (
        lock.get("revision") != model.get("revision")
        or lock.get("verified_against", {}).get("canonical_manifest_sha256") != base_sha
    ):
        raise RealRunnerError("model lock revision/canonical manifest differs from frozen pins")
    adapter_model = _verify_file_pin({
        "path": adapter_root / "adapter_model.safetensors",
        "sha256": adapter.get("adapter_model_sha256"),
    }, "adapter model")
    adapter_config = _verify_file_pin({
        "path": adapter_root / "adapter_config.json",
        "sha256": adapter.get("adapter_config_sha256"),
    }, "adapter config")
    value_path = _verify_file_pin(value, "value head")
    source_path = target_root / "alphaproof" / "net" / "value_head.py"
    _verify_file_pin({
        "path": source_path, "sha256": target.get("value_head_source_sha256")
    }, "target value-head source")
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=target_root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RealRunnerError("cannot resolve pinned target-repository commit") from exc
    if commit != target.get("commit"):
        raise RealRunnerError("target-repository commit differs from frozen pin")

    identity = wave.reference_identity
    expected_identity = {
        "policy_version": model.get("policy_version"),
        "behavior_version": model.get("policy_version"),
        "behavior_sha256": _require_sha(adapter.get("behavior_sha256"), "adapter.behavior_sha256"),
        "base_version": model.get("base_version"),
        "base_sha256": base_sha,
    }
    if identity != expected_identity:
        raise RealRunnerError("receipt behavior/base identity differs from frozen config")
    current_wave_id = wave[0].wave_id
    resume_pin = pins.get("resume_training_state")
    resume_training_state = None
    if mode == "smoke":
        if resume_pin is not None:
            raise RealRunnerError("smoke mode cannot consume predecessor training state")
    else:
        if current_wave_id == "wave-0001":
            if resume_pin is not None:
                raise RealRunnerError("formal wave 1 must start without resume training state")
        else:
            if not isinstance(resume_pin, Mapping):
                raise RealRunnerError("formal wave 2+ requires pinned resume training state")
            resume_training_state = _verify_file_pin(
                resume_pin, "resume training state"
            )
    tokenizer = _require_sha(pins.get("tokenizer_lock_sha256"), "tokenizer_lock_sha256")
    if {sample.tokenizer_sha256 for sample in wave} != {tokenizer}:
        raise RealRunnerError("receipt tokenizer lock differs from frozen config")
    runtime = _verify_runtime_versions(config.get("runtime_versions", {}))
    return {
        "model_root": model_root,
        "adapter_root": adapter_root,
        "adapter_model": adapter_model,
        "adapter_config": adapter_config,
        "value_head": value_path,
        "target_repo": target_root,
        "base_identity": ArtifactIdentity(str(model["base_version"]), base_sha),
        "runtime_versions": runtime,
        "resume_training_state": resume_training_state,
    }


def actor_trainable_state_sha256(model: torch.nn.Module) -> str:
    """Match ``shared_actor_bridge.trainable_state_sha256`` byte-for-byte."""

    parameters = [(name, value) for name, value in model.named_parameters() if value.requires_grad]
    total = sum(int(value.numel()) * int(value.element_size()) for _, value in parameters)
    if not parameters or total > 512 * 1024 * 1024:
        raise RealRunnerError(f"expected one bounded active adapter, got {total} trainable bytes")
    digest = hashlib.sha256()
    for name, value in sorted(parameters):
        tensor = value.detach().cpu().contiguous()
        header = _canonical_sha256({
            "name": name, "dtype": str(tensor.dtype), "shape": list(tensor.shape)
        })
        digest.update(bytes.fromhex(header))
        digest.update(tensor.view(dtype=torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def assert_only_adapter_trainable(model: torch.nn.Module, adapter_name: str) -> list[str]:
    """Fail closed if PEFT exposes parameters outside the selected adapter.

    ``PeftModel.set_adapter`` is responsible for switching ``requires_grad``.
    This check makes that version-dependent behavior explicit before hashing or
    constructing the optimizer, so the immutable behavior view cannot be
    updated accidentally.
    """

    marker = f".{adapter_name}."
    names = [name for name, value in model.named_parameters() if value.requires_grad]
    if not names:
        raise RealRunnerError(f"adapter {adapter_name!r} has no trainable parameters")
    unexpected = [name for name in names if marker not in name]
    if unexpected:
        preview = ", ".join(unexpected[:3])
        raise RealRunnerError(
            f"adapter {adapter_name!r} activation left non-selected parameters trainable: "
            f"{preview}"
        )
    return names


class PinnedReceiptBackend:
    """Single PEFT backbone with receipt-domain adapter and base identities.

    The base identity in actor receipts is the audited model-manifest digest,
    not a 15-GiB in-memory state-dict digest.  We verify the model lock before
    allocation, then guard every reference use with file-stat and tensor
    mutation-version checks while returning that receipt-domain identity.
    """

    def __init__(
        self, model: torch.nn.Module, *, policy_adapter: str, behavior_adapter: str,
        policy_version: str, base_identity: ArtifactIdentity, model_lock: Path,
    ) -> None:
        self._model = model
        self.policy_adapter = policy_adapter
        self.behavior_adapter = behavior_adapter
        self.policy_version = policy_version
        self._base_identity = base_identity
        self._model_lock = model_lock.resolve()
        self._model_lock_stat = self._stat(self._model_lock)
        self._base_versions = {
            name: int(getattr(value, "_version", 0))
            for name, value in model.named_parameters()
            if "lora_" not in name and "modules_to_save" not in name
        }

    @staticmethod
    def _stat(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    @property
    def model(self) -> torch.nn.Module:
        return self._model

    def activate_adapter(self, name: str) -> None:
        if name not in {self.policy_adapter, self.behavior_adapter}:
            raise KeyError(name)
        self._model.set_adapter(name)

    def adapters_disabled(self) -> ContextManager[None]:
        return self._model.disable_adapter()

    def adapter_identity(self, name: str) -> ArtifactIdentity:
        previous = getattr(self._model, "active_adapter", self.policy_adapter)
        self.activate_adapter(name)
        try:
            assert_only_adapter_trainable(self._model, name)
            return ArtifactIdentity(self.policy_version, actor_trainable_state_sha256(self._model))
        finally:
            if isinstance(previous, str) and previous in {self.policy_adapter, self.behavior_adapter}:
                self.activate_adapter(previous)
            else:
                self.activate_adapter(self.policy_adapter)

    def base_identity(self) -> ArtifactIdentity:
        if self._stat(self._model_lock) != self._model_lock_stat:
            raise RealRunnerError("pinned model lock changed after model load")
        current = dict(self._model.named_parameters())
        for name, version in self._base_versions.items():
            if name not in current or int(getattr(current[name], "_version", -1)) != version:
                raise RealRunnerError(f"frozen base tensor changed during update: {name}")
        return self._base_identity


class Heartbeat:
    def __init__(self, interval: float = 20.0) -> None:
        self.interval = interval
        self.started = time.monotonic()
        self.stage = "initializing"
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit()

    def emit(self) -> None:
        record: dict[str, Any] = {
            "event": "online_v2_heartbeat", "stage": self.stage,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
        }
        if torch.cuda.is_available():
            record.update({
                "gpu_allocated_bytes": int(torch.cuda.memory_allocated()),
                "gpu_reserved_bytes": int(torch.cuda.memory_reserved()),
                "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            })
        print(json.dumps(record, sort_keys=True), flush=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.emit()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


def _select_dtype(name: str) -> torch.dtype:
    choices = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    try:
        return choices[name]
    except KeyError as exc:
        raise RealRunnerError(f"unsupported dtype: {name}") from exc


def _load_value_head(target_repo: Path, path: Path, hidden_size: int, device: torch.device):
    sys.path.insert(0, str(target_repo))
    try:
        module = importlib.import_module("alphaproof.net.value_head")
        if Path(module.__file__).resolve() != (
            target_repo / "alphaproof" / "net" / "value_head.py"
        ).resolve():
            raise RealRunnerError("imported value-head source is not the pinned target-repo file")
        head = module.ValueHead64(hidden_size=hidden_size, mid=256, bins=64).to(device)
        module.load_s18_head(head, path)
    finally:
        try:
            sys.path.remove(str(target_repo))
        except ValueError:
            pass
    return head


def _write_checkpoint_outputs(
    output: Path, model: torch.nn.Module, policy_adapter: str,
    value_head: torch.nn.Module, training_state: Mapping[str, Any],
    receipt: Mapping[str, Any], evidence: Mapping[str, Any],
) -> dict[str, Any]:
    final = output / "checkpoint"
    temporary = output / f".checkpoint.tmp-{os.getpid()}"
    if final.exists():
        shutil.rmtree(temporary, ignore_errors=True)
    else:
        temporary.mkdir(parents=True, exist_ok=False)
        model.set_adapter(policy_adapter)
        model.save_pretrained(
            temporary / "adapter", selected_adapters=[policy_adapter], safe_serialization=True
        )
        torch.save(value_head.state_dict(), temporary / "value_head.pt")
        torch.save(dict(training_state), temporary / "training_state.pt")
        _atomic_json(temporary / "update_receipt.json", dict(receipt))
        files = []
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            files.append({
                "path": path.relative_to(temporary).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        manifest = {
            "schema_version": "fate.online_v2.real_checkpoint.v2",
            "files": files,
            "evidence": dict(evidence),
        }
        manifest["manifest_payload_sha256"] = _canonical_sha256(manifest)
        _atomic_json(temporary / "manifest.json", manifest)
        os.replace(temporary, final)
    manifest_path = final / "manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("manifest_payload_sha256") != _canonical_sha256({
        key: value for key, value in manifest.items() if key != "manifest_payload_sha256"
    }):
        raise RealRunnerError("checkpoint manifest is corrupt")
    if manifest.get("evidence") != dict(evidence):
        raise RealRunnerError("checkpoint provenance differs from current resume inputs")
    for record in manifest["files"]:
        path = final / record["path"]
        if (
            not path.is_file() or path.stat().st_size != record["size"]
            or sha256_file(path) != record["sha256"]
        ):
            raise RealRunnerError(f"checkpoint artifact is corrupt: {path}")
    return {
        "path": str(final.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "resume_training_state": {
            "path": str((final / "training_state.pt").resolve()),
            "sha256": sha256_file(final / "training_state.pt"),
        },
    }


def prepare_update_config(
    *, joined: Sequence[str | Path], signed_receipt: Sequence[str | Path], mode: str,
    model: str | Path,
    adapter: str | Path, value_head: str | Path, target_repo: str | Path,
    behavior_adapter_name: str, output: str | Path,
) -> tuple[Path, str]:
    """Freeze one exact smoke or 20-problem formal update config."""

    if mode not in {"smoke", "formal"}:
        raise RealRunnerError("config mode must be smoke or formal")
    joined_paths = [Path(path).resolve() for path in joined]
    signed_paths = [Path(path).resolve() for path in signed_receipt]
    if not joined_paths or len(joined_paths) != len(signed_paths):
        raise RealRunnerError("joined and signed receipt paths must be nonempty and one-to-one")
    model_root = Path(model).resolve()
    adapter_root = Path(adapter).resolve()
    value_path = Path(value_head).resolve()
    target_root = Path(target_repo).resolve()
    output_path = Path(output).resolve()
    if output_path.exists():
        raise RealRunnerError(f"refusing to overwrite frozen config: {output_path}")
    receipt_pins = []
    for joined_path, signed_path in zip(joined_paths, signed_paths, strict=True):
        join_payload = _load_json(joined_path)
        signed_payload = _load_json(signed_path)
        if not isinstance(join_payload, dict) or join_payload.get("schema_version") != JOIN_SCHEMA:
            raise RealRunnerError(f"joined file has the wrong schema: {joined_path}")
        source_sha = _require_sha(join_payload.get("source_receipt_sha256"), "source receipt SHA")
        if not isinstance(signed_payload, dict) or signed_payload.get("receipt_sha256") != source_sha:
            raise RealRunnerError(
                f"signed receipt file does not match join source_receipt_sha256: {signed_path}"
            )
        receipt_pins.append({
            "path": str(joined_path), "sha256": sha256_file(joined_path),
            "source_receipt_sha256": source_sha,
            "source_receipt_path": str(signed_path),
            "source_receipt_file_sha256": sha256_file(signed_path),
        })
    wave, _ = load_join_receipts(receipt_pins)
    independent = len({sample.problem_id for sample in wave})
    expected_problems = 1 if mode == "smoke" else 20
    if independent != expected_problems:
        raise RealRunnerError(
            f"{mode} config preparer requires exactly {expected_problems} independent problems, "
            f"got {independent}"
        )
    identity = wave.reference_identity
    # ``trainable_state_sha256`` deliberately includes each PEFT parameter's
    # full name.  The producer loads the rollout adapter under an alias equal
    # to ``behavior_version``/``policy_version``; loading the same tensors
    # under a different alias changes those names and therefore their identity
    # hash.  Refuse such a config at freeze time instead of discovering the
    # mismatch only after allocating the 7B model.
    require_behavior_adapter_name(behavior_adapter_name, identity)
    model_lock_path = model_root / "reap-model-lock.json"
    adapter_model_path = adapter_root / "adapter_model.safetensors"
    adapter_config_path = adapter_root / "adapter_config.json"
    value_source = target_root / "alphaproof" / "net" / "value_head.py"
    for path in (
        model_lock_path, adapter_model_path, adapter_config_path, value_path, value_source,
    ):
        if not path.is_file():
            raise RealRunnerError(f"required frozen-config input is missing: {path}")
    model_lock = _load_json(model_lock_path)
    if (
        model_lock.get("verified_against", {}).get("canonical_manifest_sha256")
        != identity["base_sha256"]
    ):
        raise RealRunnerError("join base identity differs from model lock")
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=target_root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RealRunnerError("cannot resolve target repository commit") from exc
    import peft
    import transformers
    config = {
        "schema_version": CONFIG_SCHEMA,
        "status": "frozen",
        "mode": mode,
        "pins": {
            "receipts": receipt_pins,
            "model": {
                "path": str(model_root), "revision": model_lock.get("revision"),
                "model_lock_sha256": sha256_file(model_lock_path),
                "base_version": identity["base_version"],
                "base_sha256": identity["base_sha256"],
                "policy_version": identity["policy_version"],
            },
            "adapter": {
                "path": str(adapter_root),
                "adapter_model_sha256": sha256_file(adapter_model_path),
                "adapter_config_sha256": sha256_file(adapter_config_path),
                "behavior_sha256": identity["behavior_sha256"],
                "behavior_adapter_name": behavior_adapter_name,
                "policy_adapter_name": "online_v2_policy",
            },
            "value_head": {"path": str(value_path), "sha256": sha256_file(value_path)},
            "target_repo": {
                "path": str(target_root), "commit": commit,
                "value_head_source_sha256": sha256_file(value_source),
            },
            "tokenizer_lock_sha256": wave[0].tokenizer_sha256,
        },
        "runtime_versions": {
            "torch": torch.__version__, "transformers": transformers.__version__,
            "peft": peft.__version__,
        },
        "train": {
            "seed": 20261005, "device": "cuda", "dtype": "bfloat16",
            "learning_rate": 1e-4, "value_learning_rate": 3e-4,
            "weight_decay": 0.0, "micro_batch_size": 1,
            "update_epochs": 1 if mode == "smoke" else 2,
            "min_independent_problems": expected_problems, "clip_epsilon": 0.2,
            "behavior_kl_beta": 0.02, "anchor_kl_beta": 0.005,
            "target_behavior_kl": 0.01, "hard_behavior_kl_limit": 0.05,
            "hard_anchor_kl_limit": 0.1, "early_stop_kl_multiplier": 2.0,
            "entropy_coef": 0.0, "value_coef": 0.001, "max_grad_norm": 1.0,
            "old_logprob_tolerance": 5e-4,
            "pad_token_id": int(wave[0].eos_token_id),
            "gradient_checkpointing": True,
        },
    }
    _atomic_json(output_path, config)
    return output_path, sha256_file(output_path)


def prepare_smoke_config(
    *, joined: str | Path, signed_receipt: str | Path, model: str | Path,
    adapter: str | Path, value_head: str | Path, target_repo: str | Path,
    behavior_adapter_name: str, output: str | Path,
) -> tuple[Path, str]:
    """Backward-compatible one-problem config preparer."""
    return prepare_update_config(
        joined=[joined], signed_receipt=[signed_receipt], mode="smoke", model=model,
        adapter=adapter, value_head=value_head, target_repo=target_repo,
        behavior_adapter_name=behavior_adapter_name, output=output,
    )


def _read_committed_receipt(learner: OnlineV2Learner, wave: ValidatedRolloutWave) -> dict[str, Any]:
    with learner.ledger.locked():
        return learner.ledger.committed_receipt(
            learner.behavior_wave_id,
            policy_version=learner.behavior_policy_version,
            digest=wave.content_sha256,
        )


def execute_one_update(
    config: Mapping[str, Any], config_sha256: str, output_dir: str | Path, *, resume: bool,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    if output.exists() and not resume:
        raise RealRunnerError("output exists; use --resume to reconcile the durable ledger")
    output.mkdir(parents=True, exist_ok=True)
    done_path = output / "DONE.json"
    if done_path.exists():
        if not resume:
            raise RealRunnerError("completed output exists")
        done = _load_json(done_path)
        if done.get("config_sha256") != config_sha256:
            raise RealRunnerError("completed output belongs to a different config")
        checkpoint = Path(done["checkpoint"]["path"])
        if sha256_file(checkpoint / "manifest.json") != done["checkpoint"]["manifest_sha256"]:
            raise RealRunnerError("completed checkpoint manifest changed")
        return done

    state_path = output / "STATE.json"
    _atomic_json(state_path, {
        "state": "RUNNING", "config_sha256": config_sha256,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    heartbeat = Heartbeat()
    heartbeat.start()
    try:
        heartbeat.set_stage("load_and_validate_join_receipts")
        wave, receipt_evidence = load_join_receipts(config["pins"]["receipts"])
        verified = validate_pins(config, wave)
        train = config["train"]
        if not torch.cuda.is_available() and str(train.get("device", "cuda")).startswith("cuda"):
            raise RealRunnerError("CUDA/ROCm GPU is unavailable")
        seed = int(train["seed"])
        random.seed(seed)
        try:
            import numpy as np
            np.random.seed(seed)
        except ImportError:
            pass
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
            torch.cuda.reset_peak_memory_stats()
        device = torch.device(str(train.get("device", "cuda")))
        dtype = _select_dtype(str(train.get("dtype", "bfloat16")))

        heartbeat.set_stage("load_real_prover_and_two_lora_views")
        from peft import PeftModel
        from transformers import AutoModelForCausalLM

        base = AutoModelForCausalLM.from_pretrained(
            verified["model_root"], local_files_only=True, trust_remote_code=False,
            torch_dtype=dtype,
        ).to(device)
        policy_adapter = str(config["pins"]["adapter"]["policy_adapter_name"])
        behavior_adapter = str(config["pins"]["adapter"]["behavior_adapter_name"])
        if not policy_adapter or not behavior_adapter or policy_adapter == behavior_adapter:
            raise RealRunnerError("policy and behavior adapter names must be distinct")
        model = PeftModel.from_pretrained(
            base, verified["adapter_root"], adapter_name=policy_adapter, is_trainable=True,
        )
        model.load_adapter(
            verified["adapter_root"], adapter_name=behavior_adapter, is_trainable=True,
        )
        model.set_adapter(policy_adapter)
        assert_only_adapter_trainable(model, policy_adapter)
        if bool(train.get("gradient_checkpointing", True)):
            model.gradient_checkpointing_enable()
            if hasattr(model, "enable_input_require_grads"):
                model.enable_input_require_grads()
        model.config.use_cache = False

        model_pin = config["pins"]["model"]
        backend = PinnedReceiptBackend(
            model, policy_adapter=policy_adapter, behavior_adapter=behavior_adapter,
            policy_version=str(model_pin["policy_version"]),
            base_identity=verified["base_identity"],
            model_lock=verified["model_root"] / "reap-model-lock.json",
        )
        behavior_identity = ArtifactIdentity(
            str(model_pin["policy_version"]),
            str(config["pins"]["adapter"]["behavior_sha256"]),
        )
        manifest = AdapterReferenceManifest(
            policy_adapter=policy_adapter,
            policy_version=str(model_pin["policy_version"]),
            behavior_adapter=behavior_adapter,
            behavior=behavior_identity,
            base=verified["base_identity"],
        )
        references = SingleBackboneAdapterReferences(backend, manifest)
        model.set_adapter(policy_adapter)
        assert_only_adapter_trainable(model, policy_adapter)
        pre_policy_sha256 = actor_trainable_state_sha256(model)

        heartbeat.set_stage("load_ce64_value_head")
        hidden_size = int(getattr(model.config, "hidden_size"))
        value_head = _load_value_head(
            verified["target_repo"], verified["value_head"], hidden_size, device
        )
        policy_parameter_names = assert_only_adapter_trainable(model, policy_adapter)
        named_parameters = dict(model.named_parameters())
        policy_parameters = [named_parameters[name] for name in policy_parameter_names]
        optimizer = torch.optim.AdamW([
            {
                "params": policy_parameters,
                "lr": float(train["learning_rate"]),
                "weight_decay": float(train.get("weight_decay", 0.0)),
            },
            {
                "params": list(value_head.parameters()),
                "lr": float(train["value_learning_rate"]),
                "weight_decay": float(train.get("weight_decay", 0.0)),
            },
        ])
        learner_config = OnlineV2Config(
            clip_epsilon=float(train.get("clip_epsilon", 0.2)),
            behavior_kl_beta=float(train.get("behavior_kl_beta", 0.02)),
            anchor_kl_beta=float(train.get("anchor_kl_beta", 0.005)),
            target_behavior_kl=float(train.get("target_behavior_kl", 0.01)),
            hard_behavior_kl_limit=float(train.get("hard_behavior_kl_limit", 0.05)),
            hard_anchor_kl_limit=float(train.get("hard_anchor_kl_limit", 0.10)),
            early_stop_kl_multiplier=float(train.get("early_stop_kl_multiplier", 2.0)),
            entropy_coef=float(train.get("entropy_coef", 0.0)),
            value_coef=float(train.get("value_coef", 1e-3)),
            max_grad_norm=float(train.get("max_grad_norm", 1.0)),
            update_epochs=int(train["update_epochs"]),
            micro_batch_size=int(train["micro_batch_size"]),
            min_independent_problems=int(train["min_independent_problems"]),
            old_logprob_tolerance=float(train.get("old_logprob_tolerance", 5e-4)),
            pad_token_id=int(train.get("pad_token_id", 0)),
        )
        learner = OnlineV2Learner(
            references.policy_model, references.behavior_reference, references.base_reference,
            optimizer, behavior_policy_version=str(model_pin["policy_version"]),
            behavior_wave_id=wave[0].wave_id, ledger_path=output / "wave_ledger.json",
            value_head=value_head, config=learner_config, device=device,
            adapter_references=references,
        )

        resume_training_state = verified["resume_training_state"]
        if resume_training_state is not None and not learner.sealed:
            heartbeat.set_stage("restore_predecessor_optimizer_scheduler_rng")
            try:
                predecessor_state = torch.load(
                    resume_training_state, map_location="cpu", weights_only=False
                )
            except Exception as exc:
                raise RealRunnerError("cannot load predecessor cross-wave training state") from exc
            if not isinstance(predecessor_state, Mapping):
                raise RealRunnerError("predecessor cross-wave training state is malformed")
            learner.restore_training_state(predecessor_state)

        if learner.sealed:
            heartbeat.set_stage("resume_committed_update")
            update_receipt = _read_committed_receipt(learner, wave)
        else:
            heartbeat.set_stage("online_v2_forward_backward_update")
            update_receipt = learner.update(wave).to_dict()
        model.set_adapter(policy_adapter)
        assert_only_adapter_trainable(model, policy_adapter)
        post_policy_sha256 = actor_trainable_state_sha256(model)
        actual_behavior = backend.adapter_identity(behavior_adapter)
        if actual_behavior != behavior_identity:
            raise RealRunnerError("frozen behavior adapter changed during update")
        if backend.base_identity() != verified["base_identity"]:
            raise RealRunnerError("frozen base identity changed during update")
        next_policy_version = (
            f"{model_pin['policy_version']}+online-v2-{wave[0].wave_id}-{post_policy_sha256[:16]}"
        )
        heartbeat.set_stage("publish_checkpoint")
        checkpoint = _write_checkpoint_outputs(
            output, model, policy_adapter, value_head,
            learner.export_training_state(), update_receipt,
            {
                "config_sha256": config_sha256,
                "wave_id": wave[0].wave_id,
                "wave_sha256": wave.content_sha256,
                "receipt_files": list(receipt_evidence),
                "runtime_versions": verified["runtime_versions"],
                "pre_policy_sha256": pre_policy_sha256,
                "post_policy_sha256": post_policy_sha256,
                "next_policy_version": next_policy_version,
            },
        )
        terminal = {
            "schema_version": DONE_SCHEMA,
            "state": "DONE" if update_receipt["accepted"] else "ROLLED_BACK",
            "config_sha256": config_sha256,
            "wave_sha256": wave.content_sha256,
            "update_receipt": update_receipt,
            "pre_policy_sha256": pre_policy_sha256,
            "post_policy_sha256": post_policy_sha256,
            "next_policy_version": next_policy_version,
            "checkpoint": checkpoint,
            "gpu_peak_allocated_bytes": (
                int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0
            ),
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _atomic_json(output / ("DONE.json" if update_receipt["accepted"] else "ROLLED_BACK.json"), terminal)
        _atomic_json(state_path, terminal)
        heartbeat.set_stage("complete")
        return terminal
    except BaseException as exc:
        failed = {
            "state": "FAILED", "config_sha256": config_sha256,
            "error_type": type(exc).__name__, "error": str(exc),
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _atomic_json(output / "FAILED.json", failed)
        _atomic_json(state_path, failed)
        raise
    finally:
        heartbeat.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config, digest = load_frozen_config(args.config, args.expected_config_sha256)
        report = execute_one_update(config, digest, args.output, resume=args.resume)
    except RealRunnerError as exc:
        print(json.dumps({"event": "online_v2_fail_closed", "error": str(exc)}), flush=True)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report.get("state") in {"DONE", "ROLLED_BACK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

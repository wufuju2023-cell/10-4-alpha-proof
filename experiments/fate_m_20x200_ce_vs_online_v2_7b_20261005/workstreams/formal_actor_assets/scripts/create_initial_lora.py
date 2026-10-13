#!/usr/bin/env python3
"""Create the byte-pinned, untrained REAL-Prover 7B LoRA shared by both arms."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
import threading
import time
import traceback
import uuid


TARGET_MODULES = (
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Heartbeat:
    def __init__(self) -> None:
        self.phase = "startup"
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(20):
            gpu = "unavailable"
            try:
                import torch
                if torch.cuda.is_available():
                    gpu = (
                        f"allocated_mb={torch.cuda.memory_allocated() / 2**20:.1f} "
                        f"reserved_mb={torch.cuda.memory_reserved() / 2**20:.1f}"
                    )
            except Exception:
                pass
            print(
                f"HEARTBEAT phase={self.phase} elapsed_s={time.monotonic()-self.started:.1f} {gpu}",
                flush=True,
            )

    def __enter__(self) -> "Heartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2)


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--base-revision", required=True)
    args = parser.parse_args()

    model_path = Path(args.model).resolve()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        manifest_path = output / "formal_initial_adapter_manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"refusing to overwrite non-manifest output: {output}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            if file_sha256(output / name) != expected["sha256"]:
                raise RuntimeError(f"existing adapter file hash mismatch: {name}")
        print(f"DONE existing_verified output={output}", flush=True)
        return 0

    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=False, exist_ok=False)
    state_path = output.parent / f"{output.name}.STATE.json"
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = {"status": "RUNNING", "started_utc": started_utc, "output": str(output)}
    atomic_json(state_path, state)

    try:
        with Heartbeat() as heartbeat:
            heartbeat.phase = "imports"
            import numpy as np
            import peft
            import torch
            import transformers
            from peft import LoraConfig, get_peft_model
            from transformers import AutoModelForCausalLM

            random.seed(args.seed)
            np.random.seed(args.seed)
            torch.manual_seed(args.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(args.seed)

            heartbeat.phase = "load_base_model"
            model = AutoModelForCausalLM.from_pretrained(
                str(model_path), local_files_only=True, torch_dtype=torch.bfloat16
            )
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            model = model.to(device)
            model.eval()

            heartbeat.phase = "construct_lora"
            config = LoraConfig(
                r=16,
                lora_alpha=32,
                lora_dropout=0.02,
                target_modules=list(TARGET_MODULES),
                bias="none",
                task_type="CAUSAL_LM",
                init_lora_weights=True,
            )
            model = get_peft_model(model, config)
            trainable = sorted((name, value) for name, value in model.named_parameters() if value.requires_grad)
            if not trainable:
                raise RuntimeError("LoRA construction produced no trainable parameters")
            if any("lora_" not in name for name, _ in trainable):
                raise RuntimeError("formal initial adapter unexpectedly trains non-LoRA parameters")
            b_tensors = [value for name, value in trainable if "lora_B" in name]
            if not b_tensors or any(bool(torch.count_nonzero(value.detach()).item()) for value in b_tensors):
                raise RuntimeError("default LoRA B matrices are not exactly zero")

            heartbeat.phase = "hash_trainable_state"
            state_digest = hashlib.sha256()
            parameter_manifest = []
            for name, value in trainable:
                tensor = value.detach().cpu().contiguous()
                entry = {"name": name, "shape": list(tensor.shape), "dtype": str(tensor.dtype)}
                state_digest.update(canonical_bytes(entry))
                state_digest.update(tensor.view(dtype=torch.uint8).numpy().tobytes())
                parameter_manifest.append(entry)

            heartbeat.phase = "save_adapter"
            model.save_pretrained(str(temporary), safe_serialization=True)
            files = {}
            for path in sorted(temporary.iterdir()):
                if path.is_file():
                    files[path.name] = {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            if "adapter_model.safetensors" not in files or "adapter_config.json" not in files:
                raise RuntimeError("PEFT save did not produce the required adapter files")

            manifest = {
                "schema_version": "fate-m.formal-initial-lora.v1",
                "role": "shared_byte_identical_initial_policy_adapter_for_ce_and_online_v2",
                "trained": False,
                "function_at_initialization": "base_model_exact_in_eval_because_all_lora_B_tensors_are_zero",
                "seed": args.seed,
                "base_model_path": str(model_path),
                "base_model_revision": args.base_revision,
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "peft": peft.__version__,
                "device": str(device),
                "lora": {
                    "r": 16,
                    "alpha": 32,
                    "dropout": 0.02,
                    "bias": "none",
                    "task_type": "CAUSAL_LM",
                    "target_modules": list(TARGET_MODULES),
                },
                "trainable_parameter_count": int(sum(value.numel() for _, value in trainable)),
                "trainable_state_sha256": state_digest.hexdigest(),
                "trainable_parameters": parameter_manifest,
                "files": files,
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            manifest["manifest_payload_sha256"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
            (temporary / "formal_initial_adapter_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, output)
            state = {
                "status": "DONE",
                "started_utc": started_utc,
                "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "output": str(output),
                "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            }
            atomic_json(state_path, state)
            print(json.dumps(state, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "started_utc": started_utc,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "output": str(output),
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        }
        atomic_json(state_path, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
        # A failed temporary contains no authoritative artifact and can be
        # deterministically recreated; clean only this exact UUID directory.
        if temporary.is_dir() and temporary.parent == output.parent and temporary.name.startswith(f".{output.name}."):
            shutil.rmtree(temporary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

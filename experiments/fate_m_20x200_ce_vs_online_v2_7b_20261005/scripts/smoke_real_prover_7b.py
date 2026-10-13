#!/usr/bin/env python3
"""Minimal offline REAL-Prover 7B load/forward/generation smoke.

This script deliberately does not exercise Lean or training.  It persists enough
evidence to diagnose model/runtime compatibility and is safe to rerun.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rocm_snapshot() -> str:
    try:
        proc = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        lines = [line.strip() for line in proc.stdout.splitlines()]
        return " | ".join(
            line for line in lines if "GPU use (%)" in line or "GPU Memory Allocated" in line
        ) or "unavailable"
    except Exception as exc:  # pragma: no cover - diagnostics only
        return f"unavailable:{type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=8)
    args = parser.parse_args()

    model_dir = args.model.resolve(strict=True)
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    started = time.monotonic()
    stage = {"name": "environment"}
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(15):
            elapsed = time.monotonic() - started
            print(
                f"[heartbeat] stage={stage['name']} elapsed_seconds={elapsed:.1f} "
                f"gpu={rocm_snapshot()!r}",
                flush=True,
            )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()

    environment = {
        "python": sys.version,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "hip": torch.version.hip,
        "cuda_api_available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count(),
        "model_path": str(model_dir),
        "model_config_sha256": sha256(model_dir / "config.json"),
        "generation_config_sha256": sha256(model_dir / "generation_config.json"),
        "tokenizer_config_sha256": sha256(model_dir / "tokenizer_config.json"),
        "offline_mode": True,
    }
    write_json(output_dir / "environment.json", environment)

    timings: dict[str, float] = {}
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("ROCm/CUDA API device is unavailable")

        stage["name"] = "tokenizer-load"
        tic = time.monotonic()
        tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True, trust_remote_code=False
        )
        timings["tokenizer_load_seconds"] = time.monotonic() - tic
        print(
            f"[phase] tokenizer-loaded seconds={timings['tokenizer_load_seconds']:.3f} "
            f"vocab_size={len(tokenizer)}",
            flush=True,
        )

        stage["name"] = "model-load"
        tic = time.monotonic()
        model = AutoModelForCausalLM.from_pretrained(
            model_dir,
            local_files_only=True,
            trust_remote_code=False,
            dtype=torch.bfloat16,
            device_map={"": "cuda:0"},
            low_cpu_mem_usage=True,
        )
        model.eval()
        torch.cuda.synchronize()
        timings["model_load_seconds"] = time.monotonic() - tic
        allocated_after_load = torch.cuda.memory_allocated(0)
        reserved_after_load = torch.cuda.memory_reserved(0)
        print(
            f"[phase] model-loaded seconds={timings['model_load_seconds']:.3f} "
            f"allocated_bytes={allocated_after_load} reserved_bytes={reserved_after_load}",
            flush=True,
        )

        # Short, representative Lean-facing input.  This is a runtime smoke, not
        # a capability evaluation, so generation is intentionally capped at 8 tokens.
        prompt = (
            "Complete the Lean4 proof. Return only the tactic code.\n"
            "```lean4\n"
            "import Mathlib\n"
            "theorem smoke (n : Nat) : n = n := by\n"
        )
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda:0")

        stage["name"] = "forward"
        tic = time.monotonic()
        with torch.inference_mode():
            outputs = model(**inputs)
        torch.cuda.synchronize()
        timings["forward_seconds"] = time.monotonic() - tic
        logits_shape = list(outputs.logits.shape)
        finite_last_logits = bool(torch.isfinite(outputs.logits[:, -1, :]).all().item())
        del outputs
        print(
            f"[phase] forward-complete seconds={timings['forward_seconds']:.3f} "
            f"logits_shape={logits_shape} finite_last_logits={finite_last_logits}",
            flush=True,
        )

        stage["name"] = "generation"
        tic = time.monotonic()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        torch.cuda.synchronize()
        timings["generation_seconds"] = time.monotonic() - tic
        new_tokens = generated[0, inputs["input_ids"].shape[1] :]
        decoded = tokenizer.decode(new_tokens, skip_special_tokens=False)
        peak_allocated = torch.cuda.max_memory_allocated(0)
        peak_reserved = torch.cuda.max_memory_reserved(0)

        metrics = {
            **timings,
            "total_seconds": time.monotonic() - started,
            "input_tokens": int(inputs["input_ids"].shape[1]),
            "requested_max_new_tokens": args.max_new_tokens,
            "generated_tokens": int(new_tokens.numel()),
            "generated_text": decoded,
            "logits_shape": logits_shape,
            "finite_last_logits": finite_last_logits,
            "allocated_after_load_bytes": allocated_after_load,
            "reserved_after_load_bytes": reserved_after_load,
            "peak_allocated_bytes": peak_allocated,
            "peak_reserved_bytes": peak_reserved,
        }
        write_json(output_dir / "metrics.json", metrics)
        (output_dir / "DONE").write_text("success\n", encoding="utf-8")
        print(
            f"[phase] generation-complete seconds={timings['generation_seconds']:.3f} "
            f"generated_tokens={metrics['generated_tokens']} peak_allocated_bytes={peak_allocated}",
            flush=True,
        )
        print(f"[result] generated_text={decoded!r}", flush=True)
        return 0
    except Exception as exc:
        failure = {
            "stage": stage["name"],
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "elapsed_seconds": time.monotonic() - started,
        }
        write_json(output_dir / "FAILED.json", failure)
        print(f"[failure] stage={stage['name']} type={type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 1
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())

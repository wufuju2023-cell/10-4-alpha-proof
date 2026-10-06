#!/usr/bin/env python3
"""Capture one real REAL-Prover token/log-prob request without Lean or training.

This is a bounded preflight entrypoint. It uses only an already-local model and
optionally an already-local PEFT adapter; it never downloads either artifact.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shared_actor_bridge import (  # noqa: E402
    GenerationParameters, generate_raw_candidates, trainable_state_sha256,
)

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--prompt", default="|- True")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--num-return-sequences", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=32)
    parser.add_argument("--rescore-micro-batch", type=int, default=8)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists; smoke evidence is immutable")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import LoraConfig, PeftModel, get_peft_model

    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, local_files_only=True, torch_dtype=torch.bfloat16,
    ).to("cuda" if torch.cuda.is_available() else "cpu")
    if args.adapter is not None:
        model = PeftModel.from_pretrained(model, args.adapter, is_trainable=True, local_files_only=True)
    else:
        model = get_peft_model(model, LoraConfig(
            r=16, lora_alpha=32, lora_dropout=0.02, target_modules=TARGET_MODULES,
            bias="none", task_type="CAUSAL_LM",
        ))
    model.eval()
    behavior_hash = trainable_state_sha256(model)
    generation = GenerationParameters(
        temperature=1.5, top_p=0.9, max_new_tokens=args.max_new_tokens,
        num_return_sequences=args.num_return_sequences,
    )
    captures = generate_raw_candidates(
        model=model, tokenizer=tokenizer, prompt=args.prompt, generation=generation,
        request_seed=args.seed, expected_behavior_sha256=behavior_hash,
        live_behavior_sha256=lambda: trainable_state_sha256(model),
        rescore_micro_batch=args.rescore_micro_batch,
    )
    report = {
        "schema_version": "fate.shared_actor.hf_capture_smoke.v1",
        "formal_protocol": False,
        "model_path": str(args.model.resolve()),
        "adapter_path": str(args.adapter.resolve()) if args.adapter else None,
        "behavior_sha256": behavior_hash,
        "generation": generation.__dict__,
        "prompt": args.prompt,
        "captures": [item.__dict__ for item in captures],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, default=list) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"event": "hf_capture_smoke_done", "output": str(args.output),
                      "candidates": len(captures), "behavior_sha256": behavior_hash}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

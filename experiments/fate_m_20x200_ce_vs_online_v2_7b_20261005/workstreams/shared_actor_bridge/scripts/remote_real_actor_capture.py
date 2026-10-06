#!/usr/bin/env python3
"""Bounded REAL-Prover actor-capture smoke for the FATE-M experiment.

This entrypoint never downloads, invokes Lean, or performs an optimizer step.
It creates a deterministic smoke-only LoRA, runs a small rescore calibration,
then makes exactly one frozen-shape n=64/max_new_tokens=256 actor request.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import random
import sys
import threading
import time
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shared_actor_bridge import (  # noqa: E402
    GenerationParameters,
    generate_raw_candidates,
    trainable_state_sha256,
)


TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
]
MODEL_REVISION = "fe76f68d9a88f342cb7b546307c20292fea9cced"
MODEL_CANONICAL_MANIFEST_SHA256 = (
    "7ffcbbcea4831ce54254a3514dd792d449587a397bcc3303165cdb611275ff84"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=json_default)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def immutable_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def load_problem(path: Path, problem_id: str) -> tuple[dict[str, Any], str]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("id") == problem_id:
                return row, line
    raise ValueError(f"problem_id not found: {problem_id}")


def build_root_state(problem: dict[str, Any]) -> str:
    # This is the actual root state of the chosen direct-target curriculum item.
    # It is kept explicit so the smoke is independent of Lean and cannot silently
    # pretend to have run the verifier.
    if problem["id"] != "fate_m_003_v001":
        raise ValueError("this bounded smoke currently pins fate_m_003_v001")
    return """G H K : Type*
instG : Group G
instH : Group H
instK : Group K
f : G →* H
g : H →* K
hf : Function.Surjective f
hg : Function.Surjective g
curriculum_target : Function.Surjective (g.comp f)
⊢ Function.Surjective (g.comp f)"""


def load_prompt_builder(path: Path):
    spec = importlib.util.spec_from_file_location("frozen_prompt_manage", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load pinned PromptManage")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PromptManage


class Heartbeat:
    def __init__(self, interval_seconds: float = 20.0) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.stage = "initializing"
        self.stop_event = threading.Event()
        self.torch_module: Any | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit("phase")

    def emit(self, kind: str = "heartbeat") -> None:
        event: dict[str, Any] = {
            "event": kind,
            "stage": self.stage,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
        }
        torch = self.torch_module
        if torch is not None and torch.cuda.is_available():
            event.update({
                "gpu_allocated_bytes": int(torch.cuda.memory_allocated()),
                "gpu_reserved_bytes": int(torch.cuda.memory_reserved()),
                "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            })
        print(json.dumps(event, sort_keys=True), flush=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            self.emit()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


def capture_dict(item: Any) -> dict[str, Any]:
    return {
        "candidate_index": item.candidate_index,
        "request_seed": item.request_seed,
        "prompt_token_ids": list(item.prompt_token_ids),
        "raw_completion_token_ids": list(item.raw_completion_token_ids),
        "raw_completion_old_logprobs": list(item.raw_completion_old_logprobs),
        "wall_seconds": item.wall_seconds,
        "gpu_seconds": item.gpu_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--problem-id", default="fate_m_003_v001")
    parser.add_argument("--request-seed", type=int, default=20261004)
    parser.add_argument("--adapter-seed", type=int, default=20261004)
    args = parser.parse_args()

    if args.output_dir.exists():
        parser.error("--output-dir must not already exist (evidence is immutable)")
    args.output_dir.mkdir(parents=True)
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state_path = args.output_dir / "STATE.json"
    atomic_json(state_path, {"state": "RUNNING", "started_utc": started_utc})
    heartbeat = Heartbeat()
    heartbeat.start()
    model: Any | None = None
    report: dict[str, Any] = {
        "schema_version": "fate.shared_actor.real7_capture_smoke.v1",
        "formal_protocol": False,
        "scope": "actor_capture_only_no_lean_no_training",
        "started_utc": started_utc,
    }
    try:
        heartbeat.set_stage("hash_inputs_and_build_prompt")
        if not args.model.is_dir() or not args.data.is_file() or not args.prompt_builder.is_file():
            raise FileNotFoundError("one or more pinned local inputs are missing")
        problem, problem_line = load_problem(args.data, args.problem_id)
        if sha256_bytes(problem["formal_statement"].encode("utf-8")) != problem["sha256"]:
            raise ValueError("course row formal_statement hash mismatch")
        PromptManage = load_prompt_builder(args.prompt_builder)
        current_state = build_root_state(problem)
        proof_prefix = problem["formal_statement"].rsplit("  sorry", 1)[0]
        prompt = PromptManage.build_local_incontext_prompt_str(
            proof_prefix, current_state, related_theorems=None, template="qwen",
        )
        prompt_bytes = prompt.encode("utf-8")

        bridge_files = sorted((ROOT / "src" / "shared_actor_bridge").glob("*.py"))
        bridge_manifest = [
            {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
             "sha256": sha256_file(path)} for path in bridge_files
        ]
        tokenizer_names = [
            "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
            "added_tokens.json", "vocab.json", "merges.txt",
        ]
        tokenizer_manifest = [
            {"path": name, "bytes": (args.model / name).stat().st_size,
             "sha256": sha256_file(args.model / name)}
            for name in tokenizer_names if (args.model / name).is_file()
        ]
        model_lock = args.model / "reap-model-lock.json"
        model_lock_payload = json.loads(model_lock.read_text(encoding="utf-8"))
        if model_lock_payload.get("revision") != MODEL_REVISION:
            raise ValueError("model lock revision mismatch")
        if model_lock_payload.get("verified_against", {}).get("canonical_manifest_sha256") != MODEL_CANONICAL_MANIFEST_SHA256:
            raise ValueError("model canonical manifest mismatch")

        report["inputs"] = {
            "problem_id": args.problem_id,
            "problem_line_sha256": sha256_bytes(problem_line.encode("utf-8")),
            "formal_statement_sha256": problem["sha256"],
            "curriculum_jsonl_path": str(args.data.resolve()),
            "curriculum_jsonl_sha256": sha256_file(args.data),
            "prompt_builder_path": str(args.prompt_builder.resolve()),
            "prompt_builder_sha256": sha256_file(args.prompt_builder),
            "prompt_sha256": sha256_bytes(prompt_bytes),
            "prompt_utf8_bytes": len(prompt_bytes),
            "model_path": str(args.model.resolve()),
            "model_revision": MODEL_REVISION,
            "model_lock_sha256": sha256_file(model_lock),
            "model_canonical_manifest_sha256": MODEL_CANONICAL_MANIFEST_SHA256,
            "tokenizer_manifest": tokenizer_manifest,
            "tokenizer_manifest_sha256": canonical_hash(tokenizer_manifest),
            "bridge_manifest": bridge_manifest,
            "bridge_manifest_sha256": canonical_hash(bridge_manifest),
            "entrypoint_sha256": sha256_file(Path(__file__).resolve()),
        }
        report["prompt"] = {
            "text": prompt,
            "current_state": current_state,
            "builder": "PromptManage.build_local_incontext_prompt_str",
            "template": "qwen",
            "retrieval_records": [],
            "smoke_deviation": "retrieval service not invoked; no theorem records appended",
        }

        heartbeat.set_stage("load_real_prover_7b_offline")
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer

        heartbeat.torch_module = torch
        random.seed(args.adapter_seed)
        torch.manual_seed(args.adapter_seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.adapter_seed)
        if not torch.cuda.is_available():
            raise RuntimeError("GPU is required for this smoke")
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        model_load_started = time.perf_counter()
        model = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16,
        ).to("cuda")
        base_load_seconds = time.perf_counter() - model_load_started
        # The formal initial adapter has not yet been frozen. This bounded,
        # deterministically initialized r=4 adapter is smoke-only evidence.
        lora_config = LoraConfig(
            r=4, lora_alpha=8, lora_dropout=0.0, target_modules=TARGET_MODULES,
            bias="none", task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_config)
        model.eval()
        behavior_sha256 = trainable_state_sha256(model)
        trainable_parameters = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total_parameters = sum(p.numel() for p in model.parameters())
        prompt_ids = tokenizer(prompt, add_special_tokens=True)["input_ids"]
        if len(prompt_ids) + 256 > int(model.config.max_position_embeddings):
            raise ValueError("prompt plus frozen generation exceeds model context")
        report["environment"] = {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "hip": getattr(torch.version, "hip", None),
            "transformers": __import__("transformers").__version__,
            "peft": __import__("peft").__version__,
            "device": torch.cuda.get_device_name(0),
            "gpu_total_memory_bytes": int(torch.cuda.get_device_properties(0).total_memory),
        }
        report["adapter"] = {
            "role": "smoke_only_deterministic_minimal_lora_not_formal_initial_adapter",
            "seed": args.adapter_seed,
            "config": lora_config.to_dict(),
            "trainable_parameters": int(trainable_parameters),
            "total_parameters": int(total_parameters),
            "behavior_sha256": behavior_sha256,
        }
        report["model_load_seconds"] = base_load_seconds
        report["prompt_token_ids"] = prompt_ids
        report["prompt_tokens"] = len(prompt_ids)

        heartbeat.set_stage("calibrate_rescore_microbatch_n8_max64")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        calibration_started = time.perf_counter()
        calibration = generate_raw_candidates(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            generation=GenerationParameters(
                temperature=1.5, top_p=0.9, max_new_tokens=64,
                num_return_sequences=8,
            ),
            request_seed=args.request_seed + 1,
            expected_behavior_sha256=behavior_sha256,
            live_behavior_sha256=lambda: trainable_state_sha256(model),
            rescore_micro_batch=8,
        )
        calibration_elapsed = time.perf_counter() - calibration_started
        calibration_peak = int(torch.cuda.max_memory_allocated())
        total_memory = int(torch.cuda.get_device_properties(0).total_memory)
        headroom = total_memory - calibration_peak
        # The final request can be longer, but rescoring remains streamed. Keep
        # mb=8 only with at least 32 GiB observed headroom; otherwise use mb=4.
        selected_micro_batch = 8 if headroom >= 32 * 1024**3 else 4
        report["rescore_calibration"] = {
            "generation": {"temperature": 1.5, "top_p": 0.9,
                           "max_new_tokens": 64, "num_return_sequences": 8},
            "rescore_micro_batch_tested": 8,
            "elapsed_seconds": calibration_elapsed,
            "completion_tokens": sum(len(x.raw_completion_token_ids) for x in calibration),
            "peak_allocated_bytes": calibration_peak,
            "observed_headroom_bytes": headroom,
            "selection_rule": "mb=8 iff observed GPU headroom >=32GiB, else mb=4",
            "selected_rescore_micro_batch": selected_micro_batch,
        }
        del calibration
        gc.collect()
        torch.cuda.empty_cache()

        heartbeat.set_stage("frozen_shape_actor_capture_n64_max256")
        torch.cuda.reset_peak_memory_stats()
        request_started = time.perf_counter()
        captures = generate_raw_candidates(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            generation=GenerationParameters(
                temperature=1.5, top_p=0.9, max_new_tokens=256,
                num_return_sequences=64,
            ),
            request_seed=args.request_seed,
            expected_behavior_sha256=behavior_sha256,
            live_behavior_sha256=lambda: trainable_state_sha256(model),
            rescore_micro_batch=selected_micro_batch,
        )
        request_elapsed = time.perf_counter() - request_started
        peak_allocated = int(torch.cuda.max_memory_allocated())
        peak_reserved = int(torch.cuda.max_memory_reserved())
        eos = tokenizer.eos_token_id
        capture_rows = [capture_dict(item) for item in captures]
        completion_tokens = sum(len(item.raw_completion_token_ids) for item in captures)
        truncated = [
            item.candidate_index for item in captures
            if len(item.raw_completion_token_ids) == 256
            and item.raw_completion_token_ids[-1] != eos
        ]
        finite = all(
            math.isfinite(value)
            for item in captures for value in item.raw_completion_old_logprobs
        )
        ordered = [item.candidate_index for item in captures] == list(range(64))
        seeds = sorted(set(item.request_seed for item in captures))
        prompt_ids_equal = all(tuple(prompt_ids) == item.prompt_token_ids for item in captures)
        report["capture"] = {
            "generation": {
                "temperature": 1.5,
                "top_p": 0.9,
                "max_new_tokens": 256,
                "num_return_sequences": 64,
                "seed": args.request_seed,
                "stop_token_id": eos,
                "rescore_micro_batch": selected_micro_batch,
            },
            "generation_plus_rescore_wall_seconds": request_elapsed,
            "candidate_cost_sum_seconds": sum(item.wall_seconds for item in captures),
            "completion_tokens": completion_tokens,
            "aggregate_completion_tokens_per_second": completion_tokens / request_elapsed,
            "peak_allocated_bytes": peak_allocated,
            "peak_reserved_bytes": peak_reserved,
            "all_old_logprobs_finite": finite,
            "candidate_indices_ordered": ordered,
            "request_seeds_observed": seeds,
            "all_prompt_token_ids_identical": prompt_ids_equal,
            "eos_terminated_candidates": sum(
                bool(item.raw_completion_token_ids and item.raw_completion_token_ids[-1] == eos)
                for item in captures
            ),
            "truncated_candidate_indices": truncated,
            "captures": capture_rows,
        }
        if not finite or not ordered or seeds != [args.request_seed] or not prompt_ids_equal:
            raise RuntimeError("capture invariant failed")
        if truncated:
            raise RuntimeError(f"preflight failed: truncated candidates {truncated}")

        heartbeat.set_stage("persist_evidence_and_release_gpu")
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["result"] = "PASS"
        report_path = args.output_dir / "report.json"
        immutable_json(report_path, report)
        if report_path.stat().st_size >= 50 * 1024 * 1024:
            raise RuntimeError("persistent report exceeds 50 MiB cap")
        done = {
            "state": "DONE",
            "result": "PASS",
            "report": report_path.name,
            "report_sha256": sha256_file(report_path),
            "report_bytes": report_path.stat().st_size,
            "finished_utc": report["finished_utc"],
        }
        immutable_json(args.output_dir / "DONE.json", done)
        atomic_json(state_path, done)
        print(json.dumps({"event": "actor_capture_done", **done}, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["result"] = "FAIL"
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        try:
            report_path = args.output_dir / "report.json"
            if not report_path.exists():
                immutable_json(report_path, report)
            failure = {
                "state": "FAILED",
                "result": "FAIL",
                "error": repr(exc),
                "report": report_path.name,
                "report_sha256": sha256_file(report_path),
                "finished_utc": report["finished_utc"],
            }
            immutable_json(args.output_dir / "FAILED.json", failure)
            atomic_json(state_path, failure)
        finally:
            print(json.dumps({"event": "actor_capture_failed", "error": repr(exc)},
                             sort_keys=True), flush=True)
        return 1
    finally:
        heartbeat.stop()
        model = None
        gc.collect()
        try:
            torch = heartbeat.torch_module
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

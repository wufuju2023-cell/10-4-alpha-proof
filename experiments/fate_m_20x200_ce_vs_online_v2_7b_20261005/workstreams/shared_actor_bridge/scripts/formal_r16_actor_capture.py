#!/usr/bin/env python3
"""One bounded real-7B capture using the frozen formal r16 initial adapter.

This is an actor preflight, not training and not Lean verification.  It uses
the exact formal generation shape and writes only a compact immutable report.
"""

from __future__ import annotations

from dataclasses import asdict
import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from remote_real_actor_capture import (  # noqa: E402
    MODEL_CANONICAL_MANIFEST_SHA256,
    MODEL_REVISION,
    build_root_state,
    load_problem,
    load_prompt_builder,
    sha256_bytes,
    sha256_file,
)
from shared_actor_bridge import (  # noqa: E402
    BehaviorIdentity,
    GenerationParameters,
    canonical_sha256,
    generate_raw_candidates,
    trainable_state_sha256,
)


TOKENIZER_MANIFEST_SHA256 = "5a9e4baca675e7576edd6ca1cff11ff6d0dd3bacf1d38a08494fe006bccb5a60"
FORMAL_ADAPTER_FILE_SHA256 = "326e08d17a74eec08d52127c7e011462bf1d207266cf0ca9a3a84ddc0ddde2dd"
FORMAL_TRAINABLE_STATE_SHA256 = "08115bfd89fc674b24238ea4aa7406d9e66fb8dd6daefc04826d1abbd6d1be66"


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def formal_asset_state_sha256(model: object, *, loaded_adapter_name: str = "default") -> str:
    """Reproduce create_initial_lora.py's asset hash exactly.

    PEFT embeds the runtime adapter name in every LoRA parameter name.  The
    formal asset was created under ``default`` but the serving bridge loads it
    under the session id.  Canonicalizing only that exact name segment keeps
    the asset hash independent of the serving alias while still committing to
    every tensor name, shape, dtype and byte.
    """
    import torch

    digest = hashlib.sha256()
    trainable = sorted((name, value) for name, value in model.named_parameters() if value.requires_grad)
    if not trainable:
        raise ValueError("loaded adapter exposes no trainable tensors")
    marker = f".{loaded_adapter_name}."
    normalized = 0
    for name, value in trainable:
        canonical_name = name
        if loaded_adapter_name != "default" and marker in name:
            canonical_name = name.replace(marker, ".default.")
            normalized += 1
        tensor = value.detach().cpu().contiguous()
        entry = {"name": canonical_name, "shape": list(tensor.shape), "dtype": str(tensor.dtype)}
        header = json.dumps(
            entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        digest.update(header)
        digest.update(tensor.view(dtype=torch.uint8).numpy().tobytes())
    if loaded_adapter_name != "default" and normalized != len(trainable):
        raise ValueError(
            f"adapter-name canonicalization covered {normalized}/{len(trainable)} trainable tensors"
        )
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--problem-id", default="fate_m_003_v001")
    parser.add_argument("--request-seed", type=int, default=20261004)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("--output-dir must not exist")
    args.output_dir.mkdir(parents=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_json(args.output_dir / "STATE.json", {"state": "RUNNING", "started_utc": started})
    report: dict[str, object] = {
        "schema_version": "fate.shared_actor.formal_r16_capture.v1",
        "scope": "actor_capture_only_no_lean_no_training",
        "formal_adapter": True,
        "started_utc": started,
    }
    model = None
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        for path in (args.model, args.adapter):
            if not path.is_dir():
                raise FileNotFoundError(path)
        for path in (args.data, args.prompt_builder):
            if not path.is_file():
                raise FileNotFoundError(path)
        adapter_file = args.adapter / "adapter_model.safetensors"
        if sha256_file(adapter_file) != FORMAL_ADAPTER_FILE_SHA256:
            raise ValueError("formal adapter file hash mismatch")
        model_lock = json.loads((args.model / "reap-model-lock.json").read_text(encoding="utf-8"))
        if model_lock.get("revision") != MODEL_REVISION:
            raise ValueError("base model revision mismatch")
        if model_lock.get("verified_against", {}).get("canonical_manifest_sha256") != MODEL_CANONICAL_MANIFEST_SHA256:
            raise ValueError("base canonical manifest mismatch")

        problem, problem_line = load_problem(args.data, args.problem_id)
        if sha256_bytes(problem["formal_statement"].encode()) != problem["sha256"]:
            raise ValueError("curriculum statement hash mismatch")
        PromptManage = load_prompt_builder(args.prompt_builder)
        state = build_root_state(problem)
        proof_prefix = problem["formal_statement"].rsplit("  sorry", 1)[0]
        prompt = PromptManage.build_local_incontext_prompt_str(
            proof_prefix, state, related_theorems=None, template="qwen"
        )

        print(json.dumps({"event": "formal_capture_stage", "stage": "load_base"}), flush=True)
        if not torch.cuda.is_available():
            raise RuntimeError("GPU unavailable")
        torch.manual_seed(args.request_seed)
        torch.cuda.manual_seed_all(args.request_seed)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16
        ).to("cuda")
        model = PeftModel.from_pretrained(
            base, args.adapter, is_trainable=True, adapter_name="default"
        )
        model.set_adapter("default")
        model.eval()
        formal_asset_sha = formal_asset_state_sha256(model)
        if formal_asset_sha != FORMAL_TRAINABLE_STATE_SHA256:
            raise ValueError(f"loaded formal asset state hash mismatch: {formal_asset_sha}")
        # The runtime actor identity intentionally uses the actor contract's
        # header-digest framing.  It is a different domain from the creation
        # manifest hash above, so both values are recorded rather than compared.
        behavior_sha = trainable_state_sha256(model)
        identity = BehaviorIdentity(
            policy_version="formal-initial-r16-a32-seed20261004",
            behavior_version="formal-initial-r16-a32-seed20261004",
            behavior_sha256=behavior_sha,
            base_version=f"REAL-Prover-{MODEL_REVISION[:8]}",
            base_sha256=MODEL_CANONICAL_MANIFEST_SHA256,
            tokenizer_version=f"REAL-Prover-tokenizer-{MODEL_REVISION[:8]}",
            tokenizer_sha256=TOKENIZER_MANIFEST_SHA256,
        )
        transaction = threading.RLock()
        generation = GenerationParameters(1.5, 0.9, 256, 64)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        print(json.dumps({"event": "formal_capture_stage", "stage": "generate_n64"}), flush=True)
        before = time.perf_counter()
        captures = generate_raw_candidates(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            generation=generation,
            request_id="formal-r16-fate-m-003-v001-seed20261004",
            request_seed=args.request_seed,
            expected_identity=identity,
            model_transaction=lambda: transaction,
            activate_behavior=lambda: (model.set_adapter("default"), model.eval()),
            live_identity=lambda: BehaviorIdentity(**asdict(identity)),
            rescore_micro_batch=8,
        )
        elapsed = time.perf_counter() - before
        eos = tokenizer.eos_token_id
        rows = []
        for item in captures:
            rows.append({
                "request_id": item.request_id,
                "candidate_index": item.candidate_index,
                "request_seed": item.request_seed,
                "prompt_token_ids": list(item.prompt_token_ids),
                "raw_completion_token_ids": list(item.raw_completion_token_ids),
                "raw_completion_old_logprobs": list(item.raw_completion_old_logprobs),
                "raw_completion_sampling_logprobs": list(item.raw_completion_sampling_logprobs),
                "finish_reason": item.finish_reason,
                "service_candidate_sha256": item.service_candidate_sha256,
                "wall_seconds": item.wall_seconds,
                "gpu_seconds": item.gpu_seconds,
                "decoded": tokenizer.decode(item.raw_completion_token_ids, skip_special_tokens=True),
            })
        invariants = {
            "count": len(rows),
            "indices_ordered": [row["candidate_index"] for row in rows] == list(range(64)),
            "all_finite_unwarped": all(math.isfinite(x) for row in rows for x in row["raw_completion_old_logprobs"]),
            "all_finite_warped": all(math.isfinite(x) for row in rows for x in row["raw_completion_sampling_logprobs"]),
            "eos_terminated": sum(row["finish_reason"] == "stop" and row["raw_completion_token_ids"][-1] == eos for row in rows),
            "length_terminated": sum(row["finish_reason"] == "length" for row in rows),
            "exact_curriculum_target": sum(row["decoded"].strip() == "exact curriculum_target" for row in rows),
        }
        if invariants["count"] != 64 or not invariants["indices_ordered"] or not invariants["all_finite_unwarped"] or not invariants["all_finite_warped"]:
            raise RuntimeError(f"capture invariants failed: {invariants}")
        report.update({
            "result": "PASS",
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "inputs": {
                "model": str(args.model.resolve()),
                "model_revision": MODEL_REVISION,
                "adapter": str(args.adapter.resolve()),
                "adapter_file_sha256": FORMAL_ADAPTER_FILE_SHA256,
                "trainable_state_sha256": behavior_sha,
                "formal_asset_trainable_state_sha256": formal_asset_sha,
                "data_sha256": sha256_file(args.data),
                "problem_id": args.problem_id,
                "problem_line_sha256": sha256_bytes(problem_line.encode()),
                "prompt_builder_sha256": sha256_file(args.prompt_builder),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "prompt_tokens": len(rows[0]["prompt_token_ids"]),
                "retrieval": False,
                "entrypoint_sha256": sha256_file(Path(__file__).resolve()),
                "hf_adapter_sha256": sha256_file(ROOT / "src" / "shared_actor_bridge" / "hf_adapter.py"),
                "contract_sha256": sha256_file(ROOT / "src" / "shared_actor_bridge" / "contract.py"),
            },
            "generation": {**asdict(generation), "request_seed": args.request_seed},
            "identity": asdict(identity),
            "identity_sha256": canonical_sha256(asdict(identity)),
            "elapsed_seconds": elapsed,
            "completion_tokens": sum(len(row["raw_completion_token_ids"]) for row in rows),
            "aggregate_tokens_per_second": sum(len(row["raw_completion_token_ids"]) for row in rows) / elapsed,
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "invariants": invariants,
            "captures": rows,
        })
        atomic_json(args.output_dir / "report.json", report)
        done = {
            "state": "DONE",
            "report_sha256": sha256_file(args.output_dir / "report.json"),
            "finished_utc": report["finished_utc"],
        }
        atomic_json(args.output_dir / "DONE.json", done)
        atomic_json(args.output_dir / "STATE.final.json", done)
        print(json.dumps({"event": "formal_capture_done", **done}, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        report.update({
            "result": "FAIL",
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        if not (args.output_dir / "report.json").exists():
            atomic_json(args.output_dir / "report.json", report)
        atomic_json(args.output_dir / "FAILED.json", {
            "state": "FAILED", "error_type": type(exc).__name__, "error": str(exc)
        })
        print(json.dumps({"event": "formal_capture_failed", "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
        return 1
    finally:
        model = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

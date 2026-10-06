#!/usr/bin/env python3
"""Evaluate one REAL-Prover adapter on the frozen 40-row held-out set.

The root state is captured once by the pinned Reap/Lean runtime.  This runner
then uses the pinned PromptManage transform, four paired deterministic seeds,
and an independent Lean compilation for every generated tactic.  It is
resumable at attempt granularity and never trains or mutates the checkpoint.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Mapping


HERE = Path(__file__).resolve()
EXPERIMENT_ROOT = HERE.parents[1]
WORKSTREAM_ROOT = EXPERIMENT_ROOT / "workstreams"
sys.path[:0] = [
    str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"),
    str(WORKSTREAM_ROOT / "shared_actor_bridge" / "scripts"),
]

from remote_real_actor_capture import (  # noqa: E402
    MODEL_CANONICAL_MANIFEST_SHA256,
    MODEL_REVISION,
    load_prompt_builder,
)
from shared_actor_bridge import (  # noqa: E402
    BehaviorIdentity,
    GenerationParameters,
    canonical_sha256,
    generate_raw_candidates,
    trainable_state_sha256,
)


HELDOUT_SHA256 = "a394465ef3e74666abea400496672ee97e838ddf4364c390d85362fb5d497c09"
TOKENIZER_LOCK_SHA256 = "5a9e4baca675e7576edd6ca1cff11ff6d0dd3bacf1d38a08494fe006bccb5a60"
BASE_SEED = 20261005
SPLIT_CODE = 2
ATTEMPTS = 4
GENERATION = GenerationParameters(
    temperature=1.5,
    top_p=0.9,
    max_new_tokens=512,
    num_return_sequences=1,
)
FORBIDDEN_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])(sorry|admit)(?![A-Za-z0-9_])")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def immutable_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL rows must be objects: {path}")
    return rows


def resolve_adapter(path: Path) -> Path:
    path = path.resolve()
    candidates = [path, path / "adapter"]
    # Accept either the exact adapter/checkpoint directory or the terminal run
    # root produced by the two existing learners.  CE publishes latest.json
    # with a string checkpoint; Online-v2 publishes DONE.json with
    # checkpoint.path.  This keeps the formal launch command independent of
    # arm-specific checkpoint directory layouts.
    metadata_paths = [path / "latest.json", path / "DONE.json"]
    metadata_paths.extend(sorted(path.glob("DONE.wave_*.json"), reverse=True))
    metadata_paths.extend(sorted(path.glob("FORMAL_DONE.wave_*.json"), reverse=True))
    metadata_paths.extend(sorted((path / "update").glob("FORMAL_DONE.wave_*.json"), reverse=True))
    for metadata_path in metadata_paths:
        if not metadata_path.is_file():
            continue
        value = load_json(metadata_path)
        checkpoint = value.get("checkpoint")
        if isinstance(checkpoint, dict):
            checkpoint = checkpoint.get("path")
        if isinstance(checkpoint, str) and checkpoint:
            resolved = Path(checkpoint).resolve()
            candidates.extend([resolved, resolved / "adapter"])
    for candidate in candidates:
        if (candidate / "adapter_model.safetensors").is_file() and (
            candidate / "adapter_config.json"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        f"adapter_model.safetensors + adapter_config.json missing below checkpoint: {path}"
    )


def validate_inputs(heldout: Path, prompt_pins: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if sha256_file(heldout) != HELDOUT_SHA256:
        raise ValueError("frozen held-out JSONL hash mismatch")
    rows = load_jsonl(heldout)
    expected_pairs = {(variant, family) for variant in (9, 10) for family in range(1, 21)}
    actual_pairs = {(int(row["variant_index"]), int(row["family_index"])) for row in rows}
    if len(rows) != 40 or actual_pairs != expected_pairs:
        raise ValueError("held-out set must be exactly v009-v010 across all 20 families")
    pins_doc = load_json(prompt_pins)
    if (
        pins_doc.get("schema_version") != "fate.reap_prompt_root_state_pins.v1"
        or pins_doc.get("status") != "COMPLETE"
        or pins_doc.get("session_count") != 40
        or pins_doc.get("model_loaded") is not False
    ):
        raise ValueError("root-state pins are not a complete 40-row real-Reap capture")
    pins = {str(pin["session_id"]): pin for pin in pins_doc.get("pins", [])}
    if set(pins) != {str(row["id"]) for row in rows}:
        raise ValueError("root-state pins do not cover the exact held-out IDs")
    for row in rows:
        pin = pins[str(row["id"])]
        if (
            pin.get("formal_statement_sha256") != row.get("sha256")
            or pin.get("family_index") != row.get("family_index")
            or pin.get("variant_index") != row.get("variant_index")
            or sha256_bytes(str(pin.get("root_state", "")).encode("utf-8"))
            != pin.get("root_state_sha256")
        ):
            raise ValueError(f"root-state pin/data mismatch: {row['id']}")
    return rows, pins


def attempt_seed(row: Mapping[str, Any], attempt_index: int) -> int:
    """Frozen seed rule; attempt_index is explicitly zero-based (0..3)."""
    if attempt_index not in range(ATTEMPTS):
        raise ValueError("attempt index must be zero-based in [0, 3]")
    return (
        BASE_SEED
        + 100000 * SPLIT_CODE
        + 1000 * int(row["variant_index"])
        + 10 * int(row["family_index"])
        + attempt_index
    )


def model_transaction_factory(transaction: Any):
    """Return the context-manager factory required by the shared actor bridge."""
    return lambda: transaction


def render_candidate_source(formal_statement: str, candidate: str) -> str:
    source = formal_statement.replace("\r\n", "\n")
    marker = ":= by\n  sorry"
    if source.count(marker) != 1:
        raise ValueError("formal statement lacks one canonical sorry body")
    tactic = candidate.strip()
    if not tactic:
        raise ValueError("empty generated tactic")
    body = "\n".join("  " + line for line in tactic.splitlines())
    return source.replace(marker, ":= by\n" + body, 1)


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_lean_check(
    *, lake: Path, reap_project: Path, source_path: Path, attempt_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    existing = sorted(attempt_dir.glob("lean_try_*.json"))
    try_index = len(existing) + 1
    stdout_path = attempt_dir / f"lean_try_{try_index:02d}.stdout.log"
    stderr_path = attempt_dir / f"lean_try_{try_index:02d}.stderr.log"
    started = time.monotonic()
    process = subprocess.Popen(
        [str(lake), "env", "lean", str(source_path.resolve())],
        cwd=reap_project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name != "nt"),
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_group(process)
        stdout, stderr = process.communicate()
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    output_text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    result = {
        "try_index": try_index,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "uses_sorry_warning": "declaration uses 'sorry'" in output_text,
    }
    immutable_json(attempt_dir / f"lean_try_{try_index:02d}.json", result)
    return result


def verify_candidate(
    *, row: Mapping[str, Any], candidate: str, lake: Path, reap_project: Path,
    attempt_dir: Path, timeout_seconds: float,
) -> dict[str, Any]:
    source = render_candidate_source(str(row["formal_statement"]), candidate)
    source_path = attempt_dir / "candidate.lean"
    encoded = source.encode("utf-8")
    if source_path.exists():
        if source_path.read_bytes() != encoded:
            raise RuntimeError("resumed candidate source differs from persisted source")
    else:
        source_path.write_bytes(encoded)
    placeholder = bool(FORBIDDEN_PLACEHOLDER.search(candidate))
    if placeholder:
        return {
            "status": "invalid_placeholder",
            "strict_success": False,
            "lean_tactic_executions": 0,
            "strict_lean_checks": 0,
            "lean_elapsed_seconds": 0.0,
            "tries": [],
        }
    tries = [run_lean_check(
        lake=lake, reap_project=reap_project, source_path=source_path,
        attempt_dir=attempt_dir, timeout_seconds=timeout_seconds,
    )]
    if tries[-1]["timed_out"]:
        tries.append(run_lean_check(
            lake=lake, reap_project=reap_project, source_path=source_path,
            attempt_dir=attempt_dir, timeout_seconds=timeout_seconds,
        ))
    final = tries[-1]
    strict_success = bool(
        final["returncode"] == 0 and not final["timed_out"] and not final["uses_sorry_warning"]
    )
    status = "verified" if strict_success else "timeout" if final["timed_out"] else "invalid"
    return {
        "status": status,
        "strict_success": strict_success,
        "lean_tactic_executions": len(tries),
        "strict_lean_checks": len(tries),
        "lean_elapsed_seconds": round(sum(float(item["elapsed_seconds"]) for item in tries), 6),
        "tries": tries,
    }


def generation_record(raw: Any, tokenizer: Any, prompt: str) -> dict[str, Any]:
    token_ids = list(raw.raw_completion_token_ids)
    text = tokenizer.decode(token_ids, skip_special_tokens=True)
    return {
        "schema_version": "fate.heldout_generation.v1",
        "request_id": raw.request_id,
        "candidate_index": raw.candidate_index,
        "request_seed": raw.request_seed,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "prompt_token_ids": list(raw.prompt_token_ids),
        "raw_completion_token_ids": token_ids,
        "raw_completion_old_logprobs": list(raw.raw_completion_old_logprobs),
        "raw_completion_sampling_logprobs": list(raw.raw_completion_sampling_logprobs),
        "returned_text": text,
        "finish_reason": raw.finish_reason,
        "generated_tokens": len(token_ids),
        "truncated": raw.finish_reason == "length",
        "wall_seconds": float(raw.wall_seconds),
        "gpu_seconds": float(raw.gpu_seconds),
        "service_candidate_sha256": raw.service_candidate_sha256,
    }


def summarize_tasks(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(task_results)
    if total != 40:
        raise ValueError("a terminal checkpoint evaluation requires 40 task results")
    solved = sum(bool(task["solved"]) for task in task_results)
    attempts = [attempt for task in task_results for attempt in task["attempts"]]
    first_success = {
        int(k): sum(bool(task["solved"]) and int(task["first_success_attempt"]) <= k
                    for task in task_results) / total
        for k in range(1, ATTEMPTS + 1)
    }
    by_variant = {}
    for variant in (9, 10):
        subset = [task for task in task_results if int(task["variant_index"]) == variant]
        count = sum(bool(task["solved"]) for task in subset)
        by_variant[f"v{variant:03d}"] = {
            "tasks": len(subset), "solved": count, "solve_rate": count / len(subset)
        }
    family_rates = []
    by_family = {}
    for family in range(1, 21):
        subset = [task for task in task_results if int(task["family_index"]) == family]
        count = sum(bool(task["solved"]) for task in subset)
        rate = count / len(subset)
        family_rates.append(rate)
        by_family[str(family)] = {"tasks": len(subset), "solved": count, "solve_rate": rate}
    truncations = sum(bool(item["truncated"]) for item in attempts)
    timeouts = sum(item["lean_status"] == "timeout" for item in attempts)
    return {
        "tasks": total,
        "solved_count": solved,
        "solve_rate": solved / total,
        "macro_family_solve_rate": sum(family_rates) / len(family_rates),
        "pass_at_1": first_success[1],
        "pass_at_2": first_success[2],
        "pass_at_3": first_success[3],
        "pass_at_4": first_success[4],
        "pass_at_definition": "fraction solved within the first k sequential paired-seed attempts",
        "by_variant": by_variant,
        "by_family": by_family,
        "started_attempts": len(attempts),
        "generated_tokens": sum(int(item["generated_tokens"]) for item in attempts),
        "lean_tactic_executions": sum(int(item["lean_tactic_executions"]) for item in attempts),
        "strict_lean_checks": sum(int(item["strict_lean_checks"]) for item in attempts),
        "generation_wall_seconds": round(sum(float(item["generation_wall_seconds"]) for item in attempts), 6),
        "generation_gpu_seconds": round(sum(float(item["generation_gpu_seconds"]) for item in attempts), 6),
        "lean_wall_seconds": round(sum(float(item["lean_elapsed_seconds"]) for item in attempts), 6),
        "truncated_attempts": truncations,
        "truncation_rate": truncations / len(attempts) if attempts else 0.0,
        "timeout_attempts": timeouts,
        "timeout_rate": timeouts / len(attempts) if attempts else 0.0,
    }


class Heartbeat:
    def __init__(self, counters: dict[str, Any], interval_seconds: float):
        self.counters = counters
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.stage = "preflight"
        self.torch_module: Any | None = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _gpu(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        torch = self.torch_module
        if torch is not None and torch.cuda.is_available():
            result.update({
                "gpu_allocated_bytes": int(torch.cuda.memory_allocated()),
                "gpu_reserved_bytes": int(torch.cuda.memory_reserved()),
                "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            })
        try:
            completed = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                check=True, capture_output=True, text=True, timeout=5,
            )
            first = completed.stdout.splitlines()[0].split(",")
            return result | {"gpu_utilization_percent": int(first[0].strip()),
                             "gpu_memory_used_mib": int(first[1].strip()),
                             "gpu_memory_total_mib": int(first[2].strip())}
        except Exception:
            pass
        try:
            completed = subprocess.run(
                ["rocm-smi", "--showuse", "--showmemuse", "--showmeminfo", "vram", "--json"],
                check=True, capture_output=True, text=True, timeout=5,
            )
            payload = json.loads(completed.stdout)
            card = next(value for value in payload.values() if isinstance(value, dict))
            def number(*names: str) -> int | None:
                for name in names:
                    if name in card:
                        match = re.search(r"[0-9]+", str(card[name]))
                        if match:
                            return int(match.group())
                return None
            used_bytes = number("VRAM Total Used Memory (B)")
            total_bytes = number("VRAM Total Memory (B)")
            return result | {
                "gpu_utilization_percent": number("GPU use (%)", "GPU Use (%)"),
                "gpu_memory_used_percent": number("GPU memory use (%)", "GPU Memory Use (%)"),
                "gpu_memory_used_mib": used_bytes // (1024 * 1024) if used_bytes is not None else None,
                "gpu_memory_total_mib": total_bytes // (1024 * 1024) if total_bytes is not None else None,
            }
        except Exception:
            return result | {"gpu_utilization_percent": None, "gpu_memory_used_mib": None,
                             "gpu_memory_total_mib": None}

    def emit(self, event: str = "heartbeat") -> None:
        elapsed = max(time.monotonic() - self.started, 1e-9)
        payload = {
            "event": event,
            "stage": self.stage,
            "elapsed_seconds": round(elapsed, 1),
            "completed_tasks": int(self.counters.get("completed_tasks", 0)),
            "total_tasks": 40,
            "started_attempts": int(self.counters.get("started_attempts", 0)),
            "latest_aggregate_tasks_per_hour": round(
                3600 * int(self.counters.get("completed_tasks", 0)) / elapsed, 3
            ),
            **self._gpu(),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit("phase")

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            self.emit()

    def start(self) -> None:
        self.thread.start()
        self.emit("phase")

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-label", choices=("initial", "ce_final", "online_final"), required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True,
                        help="adapter directory, or a CE/Online checkpoint containing adapter/")
    parser.add_argument("--heldout", type=Path, required=True)
    parser.add_argument("--root-state-pins", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--reap-project", type=Path, required=True)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lean-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=20.0)
    args = parser.parse_args()
    if args.lean_timeout_seconds <= 0 or not 5 <= args.heartbeat_seconds <= 30:
        parser.error("Lean timeout must be positive; heartbeat must be 5..30 seconds")
    for path in (args.heldout, args.root_state_pins, args.prompt_builder, args.lake):
        if not path.is_file():
            parser.error(f"required file missing: {path}")
    for path in (args.model, args.reap_project):
        if not path.is_dir():
            parser.error(f"required directory missing: {path}")

    counters: dict[str, Any] = {"completed_tasks": 0, "started_attempts": 0}
    heartbeat = Heartbeat(counters, args.heartbeat_seconds)
    heartbeat.start()
    started = time.monotonic()
    model = base = tokenizer = None
    try:
        heartbeat.set_stage("validate_frozen_inputs")
        rows, pins = validate_inputs(args.heldout.resolve(), args.root_state_pins.resolve())
        adapter = resolve_adapter(args.checkpoint)
        lock_doc = load_json(args.model / "reap-model-lock.json")
        if (
            lock_doc.get("revision") != MODEL_REVISION
            or lock_doc.get("verified_against", {}).get("canonical_manifest_sha256")
            != MODEL_CANONICAL_MANIFEST_SHA256
        ):
            raise ValueError("base-model pin mismatch")
        config = {
            "schema_version": "fate.heldout_checkpoint_eval.config.v1",
            "checkpoint_label": args.checkpoint_label,
            "heldout_sha256": sha256_file(args.heldout),
            "root_state_pins_sha256": sha256_file(args.root_state_pins),
            "prompt_builder_sha256": sha256_file(args.prompt_builder),
            "base_model_revision": MODEL_REVISION,
            "base_model_manifest_sha256": MODEL_CANONICAL_MANIFEST_SHA256,
            "adapter_model_sha256": sha256_file(adapter / "adapter_model.safetensors"),
            "adapter_config_sha256": sha256_file(adapter / "adapter_config.json"),
            "generation": {
                "temperature": GENERATION.temperature,
                "top_p": GENERATION.top_p,
                "max_new_tokens": GENERATION.max_new_tokens,
                "attempts_per_task": ATTEMPTS,
                "adaptive_stop": "after_first_strict_success",
            },
            "seed_rule": {
                "base_seed": BASE_SEED,
                "split_code": SPLIT_CODE,
                "attempt_index": "zero_based_0_through_3",
                "formula": "base+100000*split+1000*variant+10*family+attempt_index",
            },
            "verification": "independent lake env lean compilation; sorry/admit forbidden",
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        config_path = args.output_dir / "config.json"
        if config_path.exists():
            if load_json(config_path) != config:
                raise ValueError("resume config differs from the existing evaluation")
        else:
            immutable_json(config_path, config)
        if (args.output_dir / "DONE.json").is_file():
            print(json.dumps(load_json(args.output_dir / "report.json"), sort_keys=True), flush=True)
            return 0
        atomic_json(args.output_dir / "STATE.json", {"state": "RUNNING", "checkpoint_label": args.checkpoint_label})

        heartbeat.set_stage("load_real_7b_checkpoint")
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU unavailable")
        heartbeat.torch_module = torch
        torch.manual_seed(BASE_SEED)
        torch.cuda.manual_seed_all(BASE_SEED)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16
        ).to("cuda")
        alias = f"heldout_{args.checkpoint_label}"
        model = PeftModel.from_pretrained(
            base, adapter, is_trainable=True, adapter_name=alias
        )
        model.set_adapter(alias)
        model.eval()
        behavior_sha = trainable_state_sha256(model)
        identity = BehaviorIdentity(
            policy_version=f"heldout-{args.checkpoint_label}-{config['adapter_model_sha256'][:12]}",
            behavior_version=f"heldout-{args.checkpoint_label}-{config['adapter_model_sha256'][:12]}",
            behavior_sha256=behavior_sha,
            base_version=f"REAL-Prover-{MODEL_REVISION[:8]}",
            base_sha256=MODEL_CANONICAL_MANIFEST_SHA256,
            tokenizer_version=f"REAL-Prover-tokenizer-{MODEL_REVISION[:8]}",
            tokenizer_sha256=TOKENIZER_LOCK_SHA256,
        )
        identity.validate()
        prompt_manage = load_prompt_builder(args.prompt_builder)
        transaction = threading.Lock()

        def activate() -> None:
            model.set_adapter(alias)
            model.eval()

        task_results: list[dict[str, Any]] = []
        heartbeat.set_stage("generate_and_strict_lean_verify")
        for row in rows:
            session_id = str(row["id"])
            task_dir = args.output_dir / "tasks" / session_id
            task_dir.mkdir(parents=True, exist_ok=True)
            task_result_path = task_dir / "result.json"
            if task_result_path.is_file():
                task_result = load_json(task_result_path)
                task_results.append(task_result)
                counters["completed_tasks"] = len(task_results)
                counters["started_attempts"] += len(task_result["attempts"])
                continue
            proof_prefix = str(row["formal_statement"]).rsplit("  sorry", 1)[0]
            actor_prompt = prompt_manage.build_local_incontext_prompt_str(
                proof_prefix, str(pins[session_id]["root_state"]),
                related_theorems=None, template="qwen",
            )
            if not isinstance(actor_prompt, str) or not actor_prompt:
                raise RuntimeError(f"PromptManage returned an invalid prompt: {session_id}")
            prompt_tokens = tokenizer.encode(actor_prompt, add_special_tokens=True)
            context_limit = int(getattr(model.config, "max_position_embeddings", 0) or 0)
            if context_limit and len(prompt_tokens) + GENERATION.max_new_tokens > context_limit:
                raise ValueError(f"prompt + completion exceeds model context: {session_id}")
            prompt_record = {
                "session_id": session_id,
                "root_state_sha256": pins[session_id]["root_state_sha256"],
                "actor_prompt_sha256": sha256_bytes(actor_prompt.encode("utf-8")),
                "actor_prompt_tokens": len(prompt_tokens),
            }
            prompt_path = task_dir / "prompt.json"
            if not prompt_path.exists():
                immutable_json(prompt_path, prompt_record)
            elif load_json(prompt_path) != prompt_record:
                raise RuntimeError(f"resumed prompt differs: {session_id}")

            attempts: list[dict[str, Any]] = []
            for attempt_index in range(ATTEMPTS):
                attempt_dir = task_dir / f"attempt_{attempt_index + 1:02d}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                result_path = attempt_dir / "result.json"
                if result_path.is_file():
                    attempt_result = load_json(result_path)
                else:
                    seed = attempt_seed(row, attempt_index)
                    generation_path = attempt_dir / "generation.json"
                    if generation_path.is_file():
                        generated = load_json(generation_path)
                        if generated.get("request_seed") != seed:
                            raise RuntimeError("persisted generation seed differs")
                    else:
                        request_id = f"heldout-{args.checkpoint_label}-{session_id}-a{attempt_index + 1}"
                        last_error: BaseException | None = None
                        raw = None
                        for generation_try in range(2):
                            try:
                                raw = generate_raw_candidates(
                                    model=model, tokenizer=tokenizer, prompt=actor_prompt,
                                    generation=GENERATION, request_id=request_id,
                                    request_seed=seed, expected_identity=identity,
                                    model_transaction=model_transaction_factory(transaction),
                                    activate_behavior=activate,
                                    live_identity=lambda: identity,
                                    rescore_micro_batch=1,
                                )[0]
                                break
                            except BaseException as exc:
                                last_error = exc
                                print(json.dumps({"event": "generation_retry",
                                                  "session_id": session_id,
                                                  "attempt": attempt_index + 1,
                                                  "try": generation_try + 1,
                                                  "error_type": type(exc).__name__}), flush=True)
                        if raw is None:
                            raise RuntimeError(f"generation failed twice: {session_id}") from last_error
                        generated = generation_record(raw, tokenizer, actor_prompt)
                        immutable_json(generation_path, generated)
                    verification = verify_candidate(
                        row=row, candidate=str(generated["returned_text"]), lake=args.lake,
                        reap_project=args.reap_project, attempt_dir=attempt_dir,
                        timeout_seconds=args.lean_timeout_seconds,
                    )
                    attempt_result = {
                        "attempt": attempt_index + 1,
                        "attempt_index_zero_based": attempt_index,
                        "seed": seed,
                        "strict_success": verification["strict_success"],
                        "lean_status": verification["status"],
                        "generated_tokens": generated["generated_tokens"],
                        "truncated": generated["truncated"],
                        "finish_reason": generated["finish_reason"],
                        "generation_wall_seconds": generated["wall_seconds"],
                        "generation_gpu_seconds": generated["gpu_seconds"],
                        "lean_tactic_executions": verification["lean_tactic_executions"],
                        "strict_lean_checks": verification["strict_lean_checks"],
                        "lean_elapsed_seconds": verification["lean_elapsed_seconds"],
                    }
                    immutable_json(result_path, attempt_result)
                attempts.append(attempt_result)
                counters["started_attempts"] += 1
                if attempt_result["strict_success"]:
                    break
            first = next((int(item["attempt"]) for item in attempts if item["strict_success"]), None)
            task_result = {
                "schema_version": "fate.heldout_task_result.v1",
                "session_id": session_id,
                "family_index": int(row["family_index"]),
                "variant_index": int(row["variant_index"]),
                "solved": first is not None,
                "first_success_attempt": first,
                "attempts": attempts,
            }
            immutable_json(task_result_path, task_result)
            task_results.append(task_result)
            counters["completed_tasks"] = len(task_results)
            print(json.dumps({"event": "heldout_task_complete", "checkpoint": args.checkpoint_label,
                              "session_id": session_id, "completed": len(task_results), "total": 40,
                              "solved": task_result["solved"], "attempts": len(attempts)},
                             sort_keys=True), flush=True)

        heartbeat.set_stage("aggregate")
        metrics = summarize_tasks(task_results)
        terminal_state = "INCOMPLETE" if metrics["timeout_attempts"] else "DONE"
        report = {
            "schema_version": "fate.heldout_checkpoint_eval.report.v1",
            "state": terminal_state,
            "checkpoint_label": args.checkpoint_label,
            "behavior_sha256": behavior_sha,
            "config_sha256": sha256_file(config_path),
            "settings_fingerprint": canonical_sha256({
                key: value for key, value in config.items()
                if key not in {"checkpoint_label", "adapter_model_sha256", "adapter_config_sha256"}
            }),
            "metrics": metrics,
            "wall_seconds": round(time.monotonic() - started, 6),
            "task_results": task_results,
        }
        atomic_json(args.output_dir / "report.json", report)
        terminal = {
            "state": terminal_state,
            "checkpoint_label": args.checkpoint_label,
            "report_sha256": sha256_file(args.output_dir / "report.json"),
            "solved_count": metrics["solved_count"],
            "pass_at_4": metrics["pass_at_4"],
            "truncation_rate": metrics["truncation_rate"],
            "wall_seconds": report["wall_seconds"],
        }
        atomic_json(args.output_dir / f"{terminal_state}.json", terminal)
        atomic_json(args.output_dir / "STATE.json", terminal)
        print(json.dumps(terminal, sort_keys=True), flush=True)
        return 0 if terminal_state == "DONE" else 2
    except BaseException as exc:
        failure = {
            "state": "FAILED",
            "checkpoint_label": args.checkpoint_label,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output_dir / "FAILED.json", failure)
        atomic_json(args.output_dir / "STATE.json", failure)
        print(json.dumps({"event": "heldout_eval_failed", "error_type": type(exc).__name__,
                          "error": str(exc)}, sort_keys=True), flush=True)
        return 1
    finally:
        heartbeat.stop()
        model = base = tokenizer = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

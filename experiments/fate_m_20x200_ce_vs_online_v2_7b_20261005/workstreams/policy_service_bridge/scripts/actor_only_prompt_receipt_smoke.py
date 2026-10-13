#!/usr/bin/env python3
"""Commit one formal REAL-Prover policy receipt without starting Lean.

The smoke replays an explicitly pinned incoming Reap request artifact.  The
request is transformed with the pinned PromptManage implementation at the
same service boundary used by a live Reap request, then sampled by the formal
r16 actor.  No network access, optimizer, backward pass, or Lean process is
used.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from typing import Any, Callable, Mapping


HERE = Path(__file__).resolve()
BRIDGE_ROOT = HERE.parents[1]
WORKSTREAM_ROOT = BRIDGE_ROOT.parent
sys.path.insert(0, str(BRIDGE_ROOT / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "shared_actor_bridge" / "scripts"))

from formal_r16_actor_capture import formal_asset_state_sha256  # noqa: E402
from policy_service_bridge import (  # noqa: E402
    InProcessPolicyService,
    load_committed_receipt,
    make_live_identity_provider,
    make_peft_active_session_provider,
    parse_reap_tactic_state,
)
from policy_service_bridge.receipts import canonical_bytes, canonical_sha256, text_sha256  # noqa: E402
from remote_real_actor_capture import (  # noqa: E402
    MODEL_CANONICAL_MANIFEST_SHA256,
    MODEL_REVISION,
    load_problem,
    load_prompt_builder,
    sha256_file,
)
from shared_actor_bridge import GenerationParameters, generate_raw_candidates  # noqa: E402


SESSION_ID = "fate_m_003_v001"
ADAPTER_NAME = SESSION_ID
ADAPTER_FILE_SHA256 = "326e08d17a74eec08d52127c7e011462bf1d207266cf0ca9a3a84ddc0ddde2dd"
FORMAL_TRAINABLE_STATE_SHA256 = "08115bfd89fc674b24238ea4aa7406d9e66fb8dd6daefc04826d1abbd6d1be66"
TOKENIZER_LOCK_SHA256 = "5a9e4baca675e7576edd6ca1cff11ff6d0dd3bacf1d38a08494fe006bccb5a60"
PROBLEMS_SHA256 = "3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d"
GENERATION = GenerationParameters(1.5, 0.9, 256, 64)
ACTOR_CONFIG = {
    "schema_version": "fate.actor.actor_only_prompt_receipt_smoke.v1",
    "session_id": SESSION_ID,
    "adapter_name": ADAPTER_NAME,
    "generation": {
        "temperature": 1.5,
        "top_p": 0.9,
        "max_new_tokens": 256,
        "num_return_sequences": 64,
    },
    "prompt_template": "qwen",
    "prompt_source": "pinned_replayed_reap_request_then_point_of_use_prompt_manage",
    "retrieval": False,
    "training": False,
    "lean": False,
}
ACTOR_CONFIG_SHA256 = canonical_sha256(ACTOR_CONFIG)


def immutable_json(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be one lowercase SHA-256")


def load_incoming_artifact(path: Path, expected_file_sha256: str) -> dict[str, Any]:
    """Load and fully bind a previously captured real Reap request."""
    _require_sha256(expected_file_sha256, "incoming artifact hash")
    if not path.is_file() or sha256_file(path) != expected_file_sha256:
        raise ValueError("incoming Reap request artifact file hash mismatch")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "fate.reap.incoming_prompt_artifact.v1":
        raise ValueError("unsupported incoming Reap request artifact")
    if value.get("session_id") != SESSION_ID:
        raise ValueError("incoming artifact session differs from the pinned smoke session")
    request = value.get("normalized_request")
    if not isinstance(request, dict) or canonical_sha256(request) != value.get("normalized_request_sha256"):
        raise ValueError("normalized request hash mismatch")
    try:
        incoming_prompt = request["messages"][0]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("incoming artifact lacks the captured Reap prompt") from exc
    if text_sha256(incoming_prompt) != value.get("incoming_prompt_sha256"):
        raise ValueError("incoming Reap prompt hash mismatch")
    root_state = parse_reap_tactic_state(incoming_prompt)
    if text_sha256(root_state) != value.get("root_state_sha256"):
        raise ValueError("captured Reap root-state hash mismatch")
    source = value.get("source")
    if (not isinstance(source, dict)
            or not all(isinstance(source.get(key), str) and source[key]
                       for key in ("receipt_relative_path", "receipt_file_sha256", "receipt_payload_sha256"))):
        raise ValueError("incoming artifact lacks source-receipt provenance")
    _require_sha256(source["receipt_file_sha256"], "source receipt file hash")
    _require_sha256(source["receipt_payload_sha256"], "source receipt payload hash")
    return value


def verify_point_of_use_receipt(
    *,
    receipt_path: Path,
    tokenizer: Any,
    artifact: Mapping[str, Any],
    transformed_calls: list[dict[str, Any]],
    actor_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove that PromptManage output, actor input and receipt are identical."""
    if len(transformed_calls) != 1 or len(actor_calls) != 1:
        raise RuntimeError("expected exactly one prompt transform and one actor invocation")
    receipt = load_committed_receipt(receipt_path)
    transformed, actor = transformed_calls[0], actor_calls[0]
    direct_ids = tokenizer(transformed["actor_prompt"], add_special_tokens=True)["input_ids"]
    if direct_ids and isinstance(direct_ids[0], list):
        direct_ids = direct_ids[0]
    request = artifact["normalized_request"]
    incoming_prompt = request["messages"][0]["content"]
    conditions = {
        "receipt_session": receipt["session_id"] == SESSION_ID,
        "receipt_request": receipt["normalized_request"] == request,
        "receipt_incoming_prompt": receipt["incoming_prompt"] == incoming_prompt,
        "incoming_prompt_hash": receipt["prompt_binding"]["incoming_prompt_sha256"]
                                == artifact["incoming_prompt_sha256"],
        "transform_applied": receipt["prompt_binding"]["transform_applied"] is True,
        "transform_to_actor_text": transformed["actor_prompt"] == actor["prompt"],
        "transform_to_actor_hash": transformed["actor_prompt_sha256"] == actor["prompt_sha256"],
        "actor_to_receipt_text": actor["prompt"] == receipt["actor_prompt"] == receipt["prompt"],
        "actor_to_receipt_hash": actor["prompt_sha256"]
                                 == receipt["prompt_binding"]["actor_prompt_sha256"],
        "actor_to_receipt_tokens": actor["prompt_token_ids"] == receipt["actor_prompt_token_ids"],
        "direct_tokenization": list(direct_ids) == actor["prompt_token_ids"],
        "candidate_count": len(receipt["candidates"]) == GENERATION.num_return_sequences,
    }
    if not all(conditions.values()):
        failed = sorted(key for key, passed in conditions.items() if not passed)
        raise RuntimeError(f"point-of-use prompt/receipt binding failed: {failed}")
    return {
        "conditions": conditions,
        "receipt_sha256": receipt["receipt_sha256"],
        "incoming_prompt_sha256": artifact["incoming_prompt_sha256"],
        "root_state_sha256": artifact["root_state_sha256"],
        "actor_prompt_sha256": actor["prompt_sha256"],
        "actor_prompt_tokens": len(actor["prompt_token_ids"]),
        "candidate_count": len(receipt["candidates"]),
        "completion_tokens": sum(len(row["raw_completion_token_ids"]) for row in receipt["candidates"]),
        "receipt_file_sha256": sha256_file(receipt_path),
    }


class Heartbeat:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.stage = "starting"
        self.stop_event = threading.Event()
        self.torch: Any | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit("phase")

    def emit(self, event: str) -> None:
        row: dict[str, Any] = {
            "event": event,
            "stage": self.stage,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
        }
        if self.torch is not None and self.torch.cuda.is_available():
            row.update({
                "gpu_allocated_bytes": int(self.torch.cuda.memory_allocated()),
                "gpu_reserved_bytes": int(self.torch.cuda.memory_reserved()),
                "gpu_peak_allocated_bytes": int(self.torch.cuda.max_memory_allocated()),
            })
        print(json.dumps(row, sort_keys=True), flush=True)

    def _run(self) -> None:
        while not self.stop_event.wait(20):
            self.emit("heartbeat")

    def start(self) -> None:
        self.thread.start()
        self.emit("phase")

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--prompt-builder-sha256", required=True)
    parser.add_argument("--incoming-artifact", type=Path, required=True)
    parser.add_argument("--incoming-artifact-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("--output-dir must not exist; evidence is immutable")
    args.output_dir.mkdir(parents=True)
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_json(args.output_dir / "STATE.json", {"state": "RUNNING", "started_utc": started_utc})
    heartbeat = Heartbeat()
    heartbeat.start()
    model = base = tokenizer = None
    report: dict[str, Any] = {
        "schema_version": "fate.policy_service.actor_only_prompt_receipt_smoke.v1",
        "scope": "one_replayed_real_reap_request_official_prompt_actor_receipt_no_lean_no_training",
        "started_utc": started_utc,
    }
    try:
        heartbeat.set_stage("validate_pinned_inputs")
        _require_sha256(args.prompt_builder_sha256, "prompt-builder hash")
        artifact = load_incoming_artifact(args.incoming_artifact, args.incoming_artifact_sha256)
        if not args.prompt_builder.is_file() or sha256_file(args.prompt_builder) != args.prompt_builder_sha256:
            raise ValueError("pinned PromptManage file hash mismatch")
        if not args.problems.is_file() or sha256_file(args.problems) != PROBLEMS_SHA256:
            raise ValueError("pinned 20x200 problems file hash mismatch")
        if not args.model.is_dir() or not args.adapter.is_dir():
            raise FileNotFoundError("pinned model or adapter directory is missing")
        adapter_file = args.adapter / "adapter_model.safetensors"
        if sha256_file(adapter_file) != ADAPTER_FILE_SHA256:
            raise ValueError("formal adapter file hash mismatch")
        model_lock_path = args.model / "reap-model-lock.json"
        model_lock = json.loads(model_lock_path.read_text(encoding="utf-8"))
        if (model_lock.get("revision") != MODEL_REVISION
                or model_lock.get("verified_against", {}).get("canonical_manifest_sha256")
                != MODEL_CANONICAL_MANIFEST_SHA256):
            raise ValueError("REAL-Prover base-model lock mismatch")
        problem, problem_line = load_problem(args.problems, SESSION_ID)
        prompt_manage = load_prompt_builder(args.prompt_builder)
        proof_prefix = problem["formal_statement"].rsplit("  sorry", 1)[0]
        transformed_calls: list[dict[str, Any]] = []
        actor_calls: list[dict[str, Any]] = []

        def transform(session_id: str, incoming_prompt: str) -> str:
            if session_id != SESSION_ID or text_sha256(incoming_prompt) != artifact["incoming_prompt_sha256"]:
                raise ValueError("point-of-use request differs from the pinned real Reap capture")
            root_state = parse_reap_tactic_state(incoming_prompt)
            if text_sha256(root_state) != artifact["root_state_sha256"]:
                raise ValueError("point-of-use root state differs from its pinned hash")
            actor_prompt = prompt_manage.build_local_incontext_prompt_str(
                proof_prefix, root_state, related_theorems=None, template="qwen")
            if type(actor_prompt) is not str or not actor_prompt:
                raise ValueError("PromptManage returned an invalid actor prompt")
            transformed_calls.append({
                "actor_prompt": actor_prompt,
                "actor_prompt_sha256": text_sha256(actor_prompt),
            })
            return actor_prompt

        heartbeat.set_stage("load_formal_real_prover_actor")
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
        heartbeat.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("GPU unavailable")
        torch.manual_seed(20261004)
        torch.cuda.manual_seed_all(20261004)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16).to("cuda")
        model = PeftModel.from_pretrained(
            base, args.adapter, is_trainable=True, adapter_name=ADAPTER_NAME, local_files_only=True)
        model.set_adapter(ADAPTER_NAME)
        model.eval()
        formal_asset_sha = formal_asset_state_sha256(model, loaded_adapter_name=ADAPTER_NAME)
        if formal_asset_sha != FORMAL_TRAINABLE_STATE_SHA256:
            raise ValueError("loaded adapter differs from the frozen formal initial asset")
        lock = threading.Lock()
        identity = make_live_identity_provider(
            model=model,
            policy_version=lambda session_id: "formal-initial-r16-a32-seed20261004",
            base_version=f"REAL-Prover-{MODEL_REVISION[:8]}",
            base_sha256=MODEL_CANONICAL_MANIFEST_SHA256,
            tokenizer_version=f"REAL-Prover-tokenizer-{MODEL_REVISION[:8]}",
            tokenizer_sha256=TOKENIZER_LOCK_SHA256,
        )

        def activate(session_id: str) -> None:
            if session_id != SESSION_ID:
                raise ValueError("unexpected session")
            model.set_adapter(ADAPTER_NAME)
            model.eval()

        def capture_generator(**kwargs: Any):
            results = generate_raw_candidates(**kwargs)
            if not results:
                raise RuntimeError("actor returned no generations")
            actor_calls.append({
                "prompt": kwargs["prompt"],
                "prompt_sha256": text_sha256(kwargs["prompt"]),
                "prompt_token_ids": list(results[0].prompt_token_ids),
            })
            return results

        service = InProcessPolicyService(
            model=model,
            tokenizer=tokenizer,
            receipt_root=args.output_dir / "actor_receipts",
            identity_provider=identity,
            activate_session=activate,
            active_session_provider=make_peft_active_session_provider(model),
            model_transaction_lock=lock,
            generation=GENERATION,
            seed_namespace=("fate_m_20x200_ce_vs_online_v2_7b_20261005:"
                            f"{SESSION_ID}:actor-only-formal-r16-v1"),
            served_model_id="REAL-Prover",
            actor_config_sha256=ACTOR_CONFIG_SHA256,
            tokenizer_lock_sha256=TOKENIZER_LOCK_SHA256,
            prompt_transform=transform,
            generator=capture_generator,
            heartbeat=lambda event: print(json.dumps(dict(event), sort_keys=True), flush=True),
        )
        heartbeat.set_stage("commit_point_of_use_policy_receipt")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        response_body, receipt_path = service.handle(
            session_id=SESSION_ID, raw_body=canonical_bytes(artifact["normalized_request"]))
        verification = verify_point_of_use_receipt(
            receipt_path=receipt_path,
            tokenizer=tokenizer,
            artifact=artifact,
            transformed_calls=transformed_calls,
            actor_calls=actor_calls,
        )
        report.update({
            "result": "PASS",
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pins": {
                "model_revision": MODEL_REVISION,
                "model_canonical_manifest_sha256": MODEL_CANONICAL_MANIFEST_SHA256,
                "model_lock_sha256": sha256_file(model_lock_path),
                "adapter_file_sha256": ADAPTER_FILE_SHA256,
                "adapter_formal_asset_state_sha256": formal_asset_sha,
                "tokenizer_lock_sha256": TOKENIZER_LOCK_SHA256,
                "problems_sha256": PROBLEMS_SHA256,
                "problem_line_sha256": hashlib.sha256(problem_line.encode()).hexdigest(),
                "prompt_builder_sha256": args.prompt_builder_sha256,
                "incoming_artifact_sha256": args.incoming_artifact_sha256,
                "actor_config_sha256": ACTOR_CONFIG_SHA256,
            },
            "verification": verification,
            "artifacts": {
                "receipt": {"path": str(receipt_path), "sha256": sha256_file(receipt_path),
                            "bytes": receipt_path.stat().st_size},
                "response_body_sha256": hashlib.sha256(response_body).hexdigest(),
            },
            "gpu": {
                "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            },
            "training": False,
            "lean": False,
            "network_download": False,
        })
        immutable_json(args.output_dir / "report.json", report)
        done = {
            "state": "DONE",
            "report_sha256": sha256_file(args.output_dir / "report.json"),
            "receipt_sha256": verification["receipt_sha256"],
            "finished_utc": report["finished_utc"],
        }
        immutable_json(args.output_dir / "DONE.json", done)
        immutable_json(args.output_dir / "STATE.final.json", done)
        atomic_json(args.output_dir / "STATE.json", done)
        heartbeat.set_stage("complete")
        print(json.dumps({"event": "actor_only_receipt_smoke_done", **done}, sort_keys=True), flush=True)
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
            immutable_json(args.output_dir / "report.json", report)
        failed = {
            "state": "FAILED",
            "report_sha256": sha256_file(args.output_dir / "report.json"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "finished_utc": report["finished_utc"],
        }
        if not (args.output_dir / "FAILED.json").exists():
            immutable_json(args.output_dir / "FAILED.json", failed)
        if not (args.output_dir / "STATE.final.json").exists():
            immutable_json(args.output_dir / "STATE.final.json", failed)
        atomic_json(args.output_dir / "STATE.json", failed)
        print(json.dumps({"event": "actor_only_receipt_smoke_failed", **failed}, sort_keys=True), flush=True)
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

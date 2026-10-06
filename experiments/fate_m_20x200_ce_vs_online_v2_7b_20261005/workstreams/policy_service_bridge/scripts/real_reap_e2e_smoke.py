#!/usr/bin/env python3
"""One bounded real REAL-Prover + Reap/Lean policy-service smoke.

This runs one session (fate_m_003_v001), one Reap search step, and exactly one
frozen n=64 policy request. It does not train or update the pinned r16 adapter.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


HERE = Path(__file__).resolve()
BRIDGE_ROOT = HERE.parents[1]
WORKSTREAM_ROOT = BRIDGE_ROOT.parent
EXPERIMENT_ROOT = WORKSTREAM_ROOT.parent
sys.path.insert(0, str(BRIDGE_ROOT / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "lean_integration" / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "shared_actor_bridge" / "scripts"))

from remote_real_actor_capture import (  # noqa: E402
    MODEL_CANONICAL_MANIFEST_SHA256,
    MODEL_REVISION,
    load_problem,
    load_prompt_builder,
    sha256_bytes,
    sha256_file,
)
from formal_r16_actor_capture import formal_asset_state_sha256  # noqa: E402
from policy_service_bridge import (  # noqa: E402
    InProcessPolicyService,
    load_committed_receipt,
    make_live_identity_provider,
    make_peft_active_session_provider,
    parse_reap_tactic_state,
    start_policy_server,
)
from shared_actor_bridge import (GenerationParameters, canonical_sha256,
                                 generate_raw_candidates)  # noqa: E402
from fate_reap.session_builder import SearchOptions, write_sessions  # noqa: E402


SESSION_ID = "fate_m_003_v001"
ADAPTER_NAME = SESSION_ID
ADAPTER_FILE_SHA256 = "326e08d17a74eec08d52127c7e011462bf1d207266cf0ca9a3a84ddc0ddde2dd"
TRAINABLE_STATE_SHA256 = "08115bfd89fc674b24238ea4aa7406d9e66fb8dd6daefc04826d1abbd6d1be66"
TOKENIZER_LOCK_SHA256 = "5a9e4baca675e7576edd6ca1cff11ff6d0dd3bacf1d38a08494fe006bccb5a60"
EXPECTED_REAP_ROOT_STATE_SHA256 = "a88ec58e796390522510473c899602e5b47a0fe25390fb7112719b21da10c79b"
GENERATION = GenerationParameters(1.5, 0.9, 256, 64)
ACTOR_CONFIG = {
    "schema_version": "fate.actor.formal_r16_e2e_smoke.v1",
    "session_id": SESSION_ID,
    "adapter_name": ADAPTER_NAME,
    "generation": {"temperature": 1.5, "top_p": 0.9, "max_new_tokens": 256,
                   "num_return_sequences": 64},
    "prompt_template": "qwen",
    "prompt_source": "pinned_formal_prefix_plus_verified_incoming_reap_root_state",
    "retrieval": False,
}
ACTOR_CONFIG_SHA256 = canonical_sha256(ACTOR_CONFIG)


def _raise_termination(signum: int, _frame: object) -> None:
    raise InterruptedError(f"received termination signal {signum}")


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort bounded teardown for Lean and all of its descendants."""
    if process.poll() is not None:
        return
    if os.name != "nt":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=10)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def atomic_json(path: Path, value: object) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def immutable_json(path: Path, value: object) -> None:
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


class Heartbeat:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.stage = "preflight"
        self.torch: Any = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def emit(self, event: str = "heartbeat") -> None:
        row: dict[str, Any] = {"event": event, "stage": self.stage,
                               "elapsed_seconds": round(time.monotonic() - self.started, 1)}
        if self.torch is not None and self.torch.cuda.is_available():
            row.update({"gpu_allocated_bytes": int(self.torch.cuda.memory_allocated()),
                        "gpu_reserved_bytes": int(self.torch.cuda.memory_reserved()),
                        "gpu_utilization_percent": None})
        print(json.dumps(row, sort_keys=True), flush=True)

    def start(self) -> None:
        self.thread.start()
        self.emit("phase")

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit("phase")

    def _run(self) -> None:
        while not self.stop_event.wait(20):
            self.emit()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class ValueHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(json.dumps({"event": "value_mock_http", "message": fmt % args}), flush=True)

    def _send(self, status: int, value: object) -> None:
        body = canonical_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = json.loads(body)
            if "/value/v1/chat/completions" not in self.path or int(request.get("n", 0)) != 1:
                raise ValueError("expected Reap's single deterministic value request")
            event = {
                "schema_version": "fate.value_mock.request.v1",
                "path": self.path,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "model": request.get("model"),
                "n": request.get("n"),
            }
            server = self.server
            with server.event_lock:  # type: ignore[attr-defined]
                with server.event_path.open("ab") as stream:  # type: ignore[attr-defined]
                    stream.write(canonical_bytes(event) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            print(json.dumps({"event": "value_mock_request", "path": self.path}, sort_keys=True), flush=True)
            self._send(200, {"id": "value-only-mock", "object": "chat.completion",
                             "created": 0, "model": request.get("model", "REAL-Prover"),
                             "choices": [{"index": 0, "message": {"role": "assistant",
                                          "content": '{"score":0}'}, "finish_reason": "stop"}]})
        except Exception as exc:
            self._send(400, {"error": type(exc).__name__})


class ValueServer(ThreadingHTTPServer):
    daemon_threads = True


def save_observer_tree(observer_path: Path, tree_path: Path) -> dict[str, Any]:
    checkpoints = []
    if observer_path.is_file():
        for line in observer_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("kind") == "checkpoint" and isinstance(row.get("tree"), dict):
                    checkpoints.append(row["tree"])
    if not checkpoints:
        return {"present": False, "reason": "observer emitted no checkpoint tree"}
    tree = checkpoints[-1]
    with tree_path.open("xb") as stream:
        stream.write(json.dumps(tree, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"present": True, **file_record(tree_path), "checkpoint_count": len(checkpoints)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--reap-project", type=Path, default=Path("/tmp/fate-m-reap428/runtime"))
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/fate-m-policy-e2e-fate_m_003_v001"))
    parser.add_argument("--lean-timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("--output-dir already exists; immutable smoke evidence is never overwritten")
    args.output_dir.mkdir(parents=True)
    for path in (args.model, args.adapter, args.reap_project):
        if not path.is_dir():
            parser.error(f"pinned directory missing: {path}")
    for path in (args.problems, args.prompt_builder, args.lake):
        if not path.is_file():
            parser.error(f"pinned file missing: {path}")

    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state_path = args.output_dir / "STATE.json"
    atomic_json(state_path, {"state": "RUNNING", "started_utc": started_utc})
    heartbeat = Heartbeat()
    heartbeat.start()
    previous_sigterm = signal.signal(signal.SIGTERM, _raise_termination)
    policy_server = value_server = None
    policy_thread = value_thread = None
    lean_process: subprocess.Popen[bytes] | None = None
    model = base = tokenizer = policy_service = identity = activate = None
    prompt_transform_calls: list[dict[str, Any]] = []
    actor_calls: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": "fate.policy_service.real_reap_e2e_smoke.v1",
        "scope": "one_real_policy_request_one_lean_step_no_training",
        "session_id": SESSION_ID,
        "started_utc": started_utc,
    }
    try:
        heartbeat.set_stage("validate_pins_and_compile_session")
        adapter_file = args.adapter / "adapter_model.safetensors"
        if sha256_file(adapter_file) != ADAPTER_FILE_SHA256:
            raise ValueError("formal r16 adapter file hash mismatch")
        model_lock_path = args.model / "reap-model-lock.json"
        model_lock = json.loads(model_lock_path.read_text(encoding="utf-8"))
        if model_lock.get("revision") != MODEL_REVISION or model_lock.get("verified_against", {}).get(
                "canonical_manifest_sha256") != MODEL_CANONICAL_MANIFEST_SHA256:
            raise ValueError("REAL-Prover base model pin mismatch")
        problem, problem_line = load_problem(args.problems, SESSION_ID)
        if sha256_bytes(problem["formal_statement"].encode()) != problem["sha256"]:
            raise ValueError("curriculum theorem source hash mismatch")
        prompt_manage = load_prompt_builder(args.prompt_builder)
        proof_prefix = problem["formal_statement"].rsplit("  sorry", 1)[0]

        def transform_reap_prompt(session_id: str, incoming_prompt: str) -> str:
            if session_id != SESSION_ID:
                raise ValueError("official prompt transform received an unexpected session")
            root_state = parse_reap_tactic_state(incoming_prompt)
            root_state_sha = sha256_bytes(root_state.encode("utf-8"))
            if root_state_sha != EXPECTED_REAP_ROOT_STATE_SHA256:
                raise ValueError("incoming Reap root state differs from the pinned verified root")
            actor_prompt = prompt_manage.build_local_incontext_prompt_str(
                proof_prefix, root_state, related_theorems=None, template="qwen")
            if type(actor_prompt) is not str or not actor_prompt:
                raise ValueError("pinned PromptManage returned an invalid actor prompt")
            prompt_transform_calls.append({
                "session_id": session_id,
                "incoming_prompt": incoming_prompt,
                "incoming_prompt_sha256": sha256_bytes(incoming_prompt.encode("utf-8")),
                "root_state": root_state,
                "root_state_sha256": root_state_sha,
                "actor_prompt": actor_prompt,
                "actor_prompt_sha256": sha256_bytes(actor_prompt.encode("utf-8")),
            })
            return actor_prompt

        heartbeat.set_stage("load_pinned_real_model_adapter")
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
        heartbeat.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU unavailable")
        torch.manual_seed(20261004)
        torch.cuda.manual_seed_all(20261004)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16).to("cuda")
        # PEFT keeps the frozen adapter parameters hashable as the behavior
        # state; this entrypoint never creates an optimizer or calls backward.
        model = PeftModel.from_pretrained(base, args.adapter, is_trainable=True,
                                          adapter_name=ADAPTER_NAME)
        model.set_adapter(ADAPTER_NAME)
        model.eval()
        from shared_actor_bridge import trainable_state_sha256
        formal_asset_sha = formal_asset_state_sha256(model, loaded_adapter_name=ADAPTER_NAME)
        if formal_asset_sha != TRAINABLE_STATE_SHA256:
            raise ValueError("loaded formal r16 adapter state hash mismatch")
        # The asset manifest and live actor identity deliberately use different
        # hash framing domains.  Verify and retain both instead of comparing the
        # actor-contract digest with the asset-creation digest.
        live_adapter_sha = trainable_state_sha256(model)
        lock = threading.Lock()  # deliberately non-reentrant; service owns full transaction
        identity = make_live_identity_provider(
            model=model, policy_version=lambda sid: "formal-initial-r16-a32-seed20261004",
            base_version=f"REAL-Prover-{MODEL_REVISION[:8]}",
            base_sha256=MODEL_CANONICAL_MANIFEST_SHA256,
            tokenizer_version=f"REAL-Prover-tokenizer-{MODEL_REVISION[:8]}",
            tokenizer_sha256=TOKENIZER_LOCK_SHA256)

        def activate(session_id: str) -> None:
            if session_id != SESSION_ID:
                raise ValueError("unexpected session id")
            model.set_adapter(ADAPTER_NAME)
            model.eval()

        def capture_actor_call(**kwargs: Any):
            # The online learner validates q_old with micro_batch_size=1.  BF16
            # GEMMs can be batch-shape dependent enough to move a token log-p
            # by O(1e-2), so the actor must rescore with the same batch shape;
            # loosening the fail-closed tolerance would hide a real mismatch.
            kwargs["rescore_micro_batch"] = 1
            results = generate_raw_candidates(**kwargs)
            if not results:
                raise RuntimeError("actor returned no generations")
            actor_calls.append({
                "prompt": kwargs["prompt"],
                "prompt_sha256": sha256_bytes(kwargs["prompt"].encode("utf-8")),
                "prompt_token_ids": list(results[0].prompt_token_ids),
            })
            return results

        value_events_path = args.output_dir / "value_requests.jsonl"
        value_server = ValueServer(("127.0.0.1", 0), ValueHandler)
        value_server.event_path = value_events_path  # type: ignore[attr-defined]
        value_server.event_lock = threading.Lock()  # type: ignore[attr-defined]
        value_thread = threading.Thread(target=value_server.serve_forever, daemon=True)
        value_thread.start()
        value_url = f"http://127.0.0.1:{value_server.server_address[1]}/sessions/{SESSION_ID}/value/v1"
        policy_service = InProcessPolicyService(
            model=model, tokenizer=tokenizer, receipt_root=args.output_dir / "actor_receipts",
            identity_provider=identity, activate_session=activate,
            active_session_provider=make_peft_active_session_provider(model),
            model_transaction_lock=lock, generation=GENERATION,
            seed_namespace=f"{EXPERIMENT_ROOT.name}:{SESSION_ID}:formal-r16-v1",
            # Reap's pinned `set_option reap.model` is the request model field.
            served_model_id="REAL-Prover",
            actor_config_sha256=ACTOR_CONFIG_SHA256,
            tokenizer_lock_sha256=TOKENIZER_LOCK_SHA256,
            prompt_transform=transform_reap_prompt,
            generator=capture_actor_call,
            heartbeat=lambda event: print(json.dumps(dict(event), sort_keys=True), flush=True))
        policy_server, policy_thread = start_policy_server(policy_service, host="127.0.0.1", port=0)
        policy_url = (f"http://127.0.0.1:{policy_server.server_address[1]}"
                      f"/sessions/{{session_id}}/policy/v1")

        problems_sha = sha256_file(args.problems)
        generated_dir = args.output_dir / "theorems"
        manifest_path = args.output_dir / "sessions.jsonl"
        count = write_sessions(
            [problem], generated_dir, manifest_path,
            SearchOptions(num_samples=64, max_tokens=256, max_steps=1, max_goals=64,
                          temperature_percent=150),
            policy_url, value_url, problems_sha, MODEL_CANONICAL_MANIFEST_SHA256)
        if count != 1:
            raise RuntimeError("session_builder did not emit exactly one session")
        manifest_rows = [
            json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(manifest_rows) != 1 or manifest_rows[0].get("session_id") != SESSION_ID:
            raise RuntimeError("session manifest did not retain the requested single session")
        # The curriculum's persisted family_index is authoritative; it is not
        # necessarily encoded by the numeric segment in the external source id.
        theorem = Path(manifest_rows[0]["theorem_file"])
        if not theorem.is_file():
            raise FileNotFoundError(theorem)

        heartbeat.set_stage("run_pinned_reap_lean_max_steps_1_n64")
        observer_path = args.output_dir / "observer.jsonl"
        tree_path = args.output_dir / "raw_tree.json"
        stdout_path = args.output_dir / "lean.stdout.log"
        stderr_path = args.output_dir / "lean.stderr.log"
        env = os.environ.copy()
        env.update({"REAP_SESSION_ID": SESSION_ID,
                    "REAP_SESSION_DIR": str(args.output_dir / "session"),
                    "REAP_POLICY_ENDPOINT": policy_url.format(session_id=SESSION_ID),
                    "REAP_VALUE_ENDPOINT": value_url,
                    "REAP_OBSERVER_PATH": str(observer_path),
                    "REAP_TREE_ID": f"{SESSION_ID}-e2e-smoke",
                    "REAP_POLICY_VERSION": "0"})
        (args.output_dir / "session").mkdir()
        begin = time.monotonic()
        lean_process = subprocess.Popen(
            [str(args.lake), "env", "lean", str(theorem)], cwd=args.reap_project,
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"))
        try:
            stdout_bytes, stderr_bytes = lean_process.communicate(timeout=args.lean_timeout_seconds)
            stdout_path.write_bytes(stdout_bytes)
            stderr_path.write_bytes(stderr_bytes)
            lean_rc, timed_out = lean_process.returncode, False
        except subprocess.TimeoutExpired:
            terminate_process_group(lean_process)
            stdout_bytes, stderr_bytes = lean_process.communicate()
            stdout_path.write_bytes(stdout_bytes)
            stderr_path.write_bytes(stderr_bytes)
            lean_rc, timed_out = None, True
        lean_elapsed = time.monotonic() - begin
        raw_tree = save_observer_tree(observer_path, tree_path)
        lean_result = {"schema_version": "fate.reap.lean_smoke_result.v1",
                       "returncode": lean_rc, "timed_out": timed_out,
                       "elapsed_seconds": lean_elapsed,
                       "stdout_sha256": sha256_file(stdout_path),
                       "stderr_sha256": sha256_file(stderr_path),
                       "observer": file_record(observer_path) if observer_path.is_file() else None,
                       "raw_tree": raw_tree}
        immutable_json(args.output_dir / "lean_result.json", lean_result)
        receipt_files = sorted(
            path for path in (args.output_dir / "actor_receipts").rglob("*.json")
            if path.name != ".sequence.json"
        )
        if len(receipt_files) != 1:
            raise RuntimeError(f"expected exactly one committed policy receipt, got {len(receipt_files)}")
        receipt = load_committed_receipt(receipt_files[0])
        if len(prompt_transform_calls) != 1 or len(actor_calls) != 1:
            raise RuntimeError("expected exactly one prompt transform and one actor call")
        transformed = prompt_transform_calls[0]
        actor_call = actor_calls[0]
        direct_token_ids = tokenizer(actor_call["prompt"], add_special_tokens=True)["input_ids"]
        if direct_token_ids and isinstance(direct_token_ids[0], list):
            direct_token_ids = direct_token_ids[0]
        if (receipt["incoming_prompt"] != transformed["incoming_prompt"]
                or receipt["prompt_binding"]["incoming_prompt_sha256"] != transformed["incoming_prompt_sha256"]
                or receipt["actor_prompt"] != actor_call["prompt"]
                or receipt["prompt_binding"]["actor_prompt_sha256"] != actor_call["prompt_sha256"]
                or receipt["actor_prompt_token_ids"] != actor_call["prompt_token_ids"]
                or receipt["actor_prompt_token_ids"] != list(direct_token_ids)):
            raise RuntimeError("receipt actor prompt/hash/tokens differ from the actual actor call")
        receipt_hashes = [file_record(path) for path in receipt_files]
        value_events = [line for line in value_events_path.read_text(encoding="utf-8").splitlines() if line]
        if len(value_events) != 1:
            raise RuntimeError(f"expected exactly one persisted value request, got {len(value_events)}")
        session_artifacts = {}
        for name in ("result.json", "progress.jsonl", "wall_clock.jsonl", "raw_tree.json"):
            path = args.output_dir / "session" / name
            if path.is_file():
                session_artifacts[name] = file_record(path)
        source_artifacts = {}
        for name, path in {
            "entrypoint": HERE,
            "policy_service": BRIDGE_ROOT / "src" / "policy_service_bridge" / "service.py",
            "actor_adapter": WORKSTREAM_ROOT / "shared_actor_bridge" / "src" / "shared_actor_bridge" / "hf_adapter.py",
            "session_builder": WORKSTREAM_ROOT / "lean_integration" / "src" / "fate_reap" / "session_builder.py",
            "lake": args.lake,
            "reap_lake_manifest": args.reap_project / "lake-manifest.json",
            "reap_lean_toolchain": args.reap_project / "lean-toolchain",
        }.items():
            if path.is_file():
                source_artifacts[name] = file_record(path)
        import peft
        import transformers
        report.update({
            "result": "PASS" if lean_rc == 0 and not timed_out and raw_tree.get("present") else "LEAN_NONZERO_OR_INCOMPLETE",
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pins": {"model_revision": MODEL_REVISION,
                     "model_canonical_manifest_sha256": MODEL_CANONICAL_MANIFEST_SHA256,
                     "model_lock_sha256": sha256_file(model_lock_path),
                     "adapter_name": ADAPTER_NAME, "adapter_file_sha256": ADAPTER_FILE_SHA256,
                     "adapter_formal_asset_state_sha256": formal_asset_sha,
                     "adapter_behavior_state_sha256": live_adapter_sha,
                     "tokenizer_lock_sha256": TOKENIZER_LOCK_SHA256,
                     "actor_config_sha256": ACTOR_CONFIG_SHA256,
                     "problems_sha256": problems_sha,
                     "problem_line_sha256": sha256_bytes(problem_line.encode()),
                     "prompt_builder_sha256": sha256_file(args.prompt_builder),
                     "incoming_reap_prompt_sha256": transformed["incoming_prompt_sha256"],
                     "reap_root_state_sha256": transformed["root_state_sha256"],
                     "actor_prompt_sha256": actor_call["prompt_sha256"],
                     "prompt_sha256": actor_call["prompt_sha256"]},
            "settings": {"generation": {"temperature": 1.5, "top_p": 0.9,
                         "max_tokens": 256, "n": 64, "rescore_micro_batch": 1},
                         "lean_max_steps": 1,
                         "lean_max_goals": 64, "lean_timeout_seconds": args.lean_timeout_seconds,
                         "training": False, "retrieval": False},
            "artifacts": {"theorem": file_record(theorem),
                          "manifest": file_record(manifest_path),
                          "policy_receipts": receipt_hashes,
                          "observer": file_record(observer_path) if observer_path.is_file() else None,
                          "raw_tree": raw_tree,
                          "lean_result": file_record(args.output_dir / "lean_result.json"),
                          "lean_stdout": file_record(stdout_path),
                          "lean_stderr": file_record(stderr_path),
                          "value_requests": file_record(value_events_path),
                          "session": session_artifacts,
                          "sources": source_artifacts},
            "environment": {"python": sys.version, "torch": torch.__version__,
                            "torch_hip": torch.version.hip,
                            "transformers": transformers.__version__, "peft": peft.__version__},
            "lean_returncode": lean_rc, "lean_timed_out": timed_out,
            "policy_receipt_count": len(receipt_hashes), "value_request_count": len(value_events),
        })
        immutable_json(args.output_dir / "report.json", report)
        terminal = "DONE" if report["result"] == "PASS" else "FAILED"
        state = {"state": terminal, "report_sha256": sha256_file(args.output_dir / "report.json"),
                 "lean_result_sha256": sha256_file(args.output_dir / "lean_result.json"),
                 "finished_utc": report["finished_utc"]}
        immutable_json(args.output_dir / f"{terminal}.json", state)
        atomic_json(state_path, state)
        heartbeat.set_stage("complete")
        return 0 if report["result"] == "PASS" else 1
    except BaseException as exc:
        report.update({"result": "FAIL", "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "error_type": type(exc).__name__, "error": str(exc),
                       "traceback": traceback.format_exc()})
        if not (args.output_dir / "report.json").exists():
            immutable_json(args.output_dir / "report.json", report)
        failed = {"state": "FAILED", "report_sha256": sha256_file(args.output_dir / "report.json"),
                  "error_type": type(exc).__name__, "error": str(exc),
                  "finished_utc": report["finished_utc"]}
        immutable_json(args.output_dir / "FAILED.json", failed)
        atomic_json(state_path, failed)
        print(json.dumps({"event": "e2e_smoke_failed", **failed}, sort_keys=True), flush=True)
        return 1
    finally:
        heartbeat.stop()
        signal.signal(signal.SIGTERM, previous_sigterm)
        if lean_process is not None:
            try:
                terminate_process_group(lean_process)
            except Exception as cleanup_exc:
                print(json.dumps({"event": "lean_cleanup_failed", "error": str(cleanup_exc)}), flush=True)
        if policy_server is not None:
            policy_server.shutdown()
            policy_server.server_close()
            policy_server = None
        if policy_thread is not None:
            policy_thread.join(timeout=5)
        if value_server is not None:
            value_server.shutdown()
            value_server.server_close()
            value_server = None
        if value_thread is not None:
            value_thread.join(timeout=5)
        policy_service = identity = activate = None
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

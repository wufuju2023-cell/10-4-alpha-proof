#!/usr/bin/env python3
"""Run one pinned 20-problem Reap wave with one REAL-Prover load.

Each problem receives at most four independent one-step searches, stopping
after the first strict Reap success.  Every attempt is retained, and the
selected terminal attempt is materialized at the problem root in the exact
shape accepted by ``join_real_e2e_receipt.py``.
"""

from __future__ import annotations

from dataclasses import asdict
import argparse
import gc
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


HERE = Path(__file__).resolve()
BRIDGE_ROOT = HERE.parents[1]
WORKSTREAM_ROOT = BRIDGE_ROOT.parent
EXPERIMENT_ROOT = WORKSTREAM_ROOT.parent
sys.path[:0] = [
    str(BRIDGE_ROOT / "src"),
    str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"),
    str(WORKSTREAM_ROOT / "lean_integration" / "src"),
    str(WORKSTREAM_ROOT / "shared_actor_bridge" / "scripts"),
]

from remote_real_actor_capture import (  # noqa: E402
    MODEL_CANONICAL_MANIFEST_SHA256, MODEL_REVISION, load_prompt_builder,
    sha256_bytes, sha256_file,
)
from policy_service_bridge import (  # noqa: E402
    InProcessPolicyService, make_live_identity_provider,
    make_peft_active_session_provider, parse_reap_tactic_state,
    start_policy_server,
)
from shared_actor_bridge import (  # noqa: E402
    GenerationParameters, canonical_sha256, trainable_state_sha256,
)
from fate_reap.session_builder import SearchOptions, write_sessions  # noqa: E402
from real_reap_e2e_smoke import (  # noqa: E402
    TOKENIZER_LOCK_SHA256, save_observer_tree, terminate_process_group,
)


SESSION_COUNT = 20
MAX_ATTEMPTS = 4
MAX_TOKENS = 512
GENERATION = GenerationParameters(1.5, 0.9, MAX_TOKENS, 64)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def immutable_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def file_record(path: Path) -> dict[str, object]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def load_wave(plan_path: Path, problems_path: Path,
              pins_path: Path) -> tuple[dict, list[dict], dict[str, dict], str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    pins_doc = json.loads(pins_path.read_text(encoding="utf-8"))
    tasks = plan.get("tasks")
    if (not isinstance(tasks, list) or len(tasks) != SESSION_COUNT
            or plan.get("expected_session_count", SESSION_COUNT) != SESSION_COUNT):
        raise ValueError("wave plan must contain exactly 20 sessions")
    problems_sha256 = sha256_file(problems_path)
    declared_problems_sha256 = plan.get("source", {}).get("problems_sha256")
    if declared_problems_sha256 is not None and declared_problems_sha256 != problems_sha256:
        raise ValueError("problems JSONL hash differs from the wave plan")
    rows: dict[str, dict] = {}
    for line_number, line in enumerate(
            problems_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        session_id = row.get("id")
        if not isinstance(session_id, str) or session_id in rows:
            raise ValueError(f"invalid or duplicate problem id at line {line_number}")
        if sha256_bytes(str(row.get("formal_statement", "")).encode("utf-8")) != row.get("sha256"):
            raise ValueError(f"formal statement hash mismatch: {session_id}")
        rows[session_id] = row
    selected = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("wave task must be an object")
        row = rows.get(task["session_id"])
        if (not row or row["id"] in seen
                or row.get("family_index") != task.get("family_index")
                or row.get("variant_index") != task.get("variant_index")
                or row.get("sha256") != task.get("formal_statement_sha256")):
            raise ValueError(f"wave task/data mismatch: {task.get('session_id')}")
        selected.append(row)
        seen.add(row["id"])
    pins = {item["session_id"]: item for item in pins_doc.get("pins", [])}
    if set(pins) != {row["id"] for row in selected}:
        raise ValueError("root-state pins do not cover the exact wave")
    if pins_doc.get("plan_sha256") != sha256_file(plan_path):
        raise ValueError("root-state pins belong to another wave plan")
    if pins_doc.get("problems_sha256") not in (None, problems_sha256):
        raise ValueError("root-state pins belong to another problems JSONL")
    for row in selected:
        pin = pins[row["id"]]
        if (pin.get("formal_statement_sha256") != row["sha256"]
                or pin.get("family_index") != row["family_index"]
                or pin.get("variant_index") != row["variant_index"]
                or not isinstance(pin.get("root_state_sha256"), str)):
            raise ValueError(f"root-state pin/data mismatch: {row['id']}")
    return plan, selected, pins, problems_sha256


def hardlink_tree(source: Path, target: Path) -> None:
    """Materialize one immutable attempt without duplicating its large receipts."""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, destination)
            except OSError:
                shutil.copy2(path, destination)


class ValueHandler(BaseHTTPRequestHandler):
    def log_message(self, _fmt: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        try:
            session_id = self.path.split("/sessions/", 1)[1].split("/", 1)[0]
            if session_id not in self.server.sessions:  # type: ignore[attr-defined]
                raise ValueError("unknown session")
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = json.loads(raw)
            if request.get("n") != 1 or "/value/v1/chat/completions" not in self.path:
                raise ValueError("invalid value request")
            event_path = self.server.output_root / session_id / "value_requests.jsonl"  # type: ignore[attr-defined]
            with self.server.event_lock:  # type: ignore[attr-defined]
                with event_path.open("ab") as stream:
                    stream.write(canonical_bytes({"session_id": session_id,
                                                  "body_sha256": hashlib.sha256(raw).hexdigest()}) + b"\n")
                    stream.flush(); os.fsync(stream.fileno())
            value = {"id": f"value-{session_id}", "object": "chat.completion", "created": 0,
                     "model": request.get("model", "REAL-Prover"), "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "{\"score\":0}"},
                     "finish_reason": "stop"}]}
            body, status = canonical_bytes(value), 200
        except Exception as exc:
            body, status = canonical_bytes({"error": type(exc).__name__}), 400
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root-state-pins", type=Path, required=True)
    parser.add_argument("--actor-config", type=Path, required=True)
    parser.add_argument("--prompt-builder", type=Path, required=True)
    parser.add_argument("--reap-project", type=Path, required=True)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--expected-adapter-model-sha256")
    parser.add_argument("--per-session-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.wave_index < 1:
        parser.error("--wave-index must be positive")
    if args.output_dir.exists():
        parser.error("--output-dir must not exist")
    args.output_dir.mkdir(parents=True)
    _plan, problems, pins, problems_sha256 = load_wave(
        args.plan, args.problems, args.root_state_pins
    )
    actor_config = json.loads(args.actor_config.read_text(encoding="utf-8"))
    adapter_alias = actor_config.get("behavior_adapter_alias")
    if not isinstance(adapter_alias, str) or not adapter_alias:
        raise ValueError("actor config lacks a behavior adapter alias")
    expected_generation = {
        "temperature": GENERATION.temperature,
        "top_p": GENERATION.top_p,
        "max_new_tokens": GENERATION.max_new_tokens,
        "num_return_sequences": GENERATION.num_return_sequences,
        "rescore_micro_batch": 1,
    }
    if actor_config.get("generation") != expected_generation:
        raise ValueError("actor config does not freeze the formal 64x512 generation contract")
    actor_config_sha = canonical_sha256(actor_config)
    prompt_manage = load_prompt_builder(args.prompt_builder)
    prefixes = {row["id"]: row["formal_statement"].rsplit("  sorry", 1)[0] for row in problems}
    model = base = policy_server = value_server = None
    policy_thread = value_thread = None
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
        adapter_file_sha = sha256_file(args.adapter / "adapter_model.safetensors")
        if (args.expected_adapter_model_sha256 is not None
                and adapter_file_sha != args.expected_adapter_model_sha256.lower()):
            raise ValueError("current arm adapter hash mismatch")
        lock_doc = json.loads((args.model / "reap-model-lock.json").read_text(encoding="utf-8"))
        if (lock_doc.get("revision") != MODEL_REVISION or
                lock_doc.get("verified_against", {}).get("canonical_manifest_sha256") != MODEL_CANONICAL_MANIFEST_SHA256):
            raise ValueError("base-model pin mismatch")
        print(json.dumps({"event": "wave_model_load_start", "sessions": SESSION_COUNT,
                          "wave_index": args.wave_index}), flush=True)
        torch.manual_seed(20261005); torch.cuda.manual_seed_all(20261005)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(args.model, local_files_only=True,
                                                    torch_dtype=torch.bfloat16).to("cuda")
        model = PeftModel.from_pretrained(base, args.adapter, is_trainable=True,
                                          adapter_name=adapter_alias)
        model.set_adapter(adapter_alias); model.eval()
        behavior_sha = trainable_state_sha256(model)
        transaction = threading.Lock()
        identity = make_live_identity_provider(
            model=model, policy_version=lambda _sid: actor_config["policy_version"],
            base_version=f"REAL-Prover-{MODEL_REVISION[:8]}", base_sha256=MODEL_CANONICAL_MANIFEST_SHA256,
            tokenizer_version=f"REAL-Prover-tokenizer-{MODEL_REVISION[:8]}",
            tokenizer_sha256=TOKENIZER_LOCK_SHA256)

        def activate(_session_id: str) -> None:
            model.set_adapter(adapter_alias); model.eval()

        def transform(session_id: str, incoming: str) -> str:
            root = parse_reap_tactic_state(incoming)
            if sha256_bytes(root.encode("utf-8")) != pins[session_id]["root_state_sha256"]:
                raise ValueError("live root state differs from captured pin")
            return prompt_manage.build_local_incontext_prompt_str(
                prefixes[session_id], root, related_theorems=None, template="qwen")

        service = InProcessPolicyService(
            model=model, tokenizer=tokenizer, receipt_root=args.output_dir / "_actor_receipts",
            identity_provider=identity, activate_session=activate,
            active_session_provider=make_peft_active_session_provider(model),
            adapter_name_for_session=lambda _sid: adapter_alias,
            model_transaction_lock=transaction, generation=GENERATION,
            seed_namespace=(f"{EXPERIMENT_ROOT.name}:formal-seed-20261005:"
                            f"wave-{args.wave_index:03d}"),
            served_model_id="REAL-Prover", actor_config_sha256=actor_config_sha,
            tokenizer_lock_sha256=TOKENIZER_LOCK_SHA256, prompt_transform=transform,
            rescore_micro_batch=1)
        policy_server, policy_thread = start_policy_server(service, host="127.0.0.1", port=0)
        value_server = ThreadingHTTPServer(("127.0.0.1", 0), ValueHandler)
        value_server.sessions = {row["id"] for row in problems}  # type: ignore[attr-defined]
        value_server.output_root = args.output_dir  # type: ignore[attr-defined]
        value_server.event_lock = threading.Lock()  # type: ignore[attr-defined]
        value_thread = threading.Thread(target=value_server.serve_forever, daemon=True); value_thread.start()
        policy_url = f"http://127.0.0.1:{policy_server.server_address[1]}/sessions/{{session_id}}/policy/v1"
        value_url = f"http://127.0.0.1:{value_server.server_address[1]}/sessions/{{session_id}}/value/v1"
        theorem_root, manifest = args.output_dir / "theorems", args.output_dir / "wave_sessions.jsonl"
        write_sessions(problems, theorem_root, manifest,
                       SearchOptions(num_samples=64, max_tokens=MAX_TOKENS,
                                     max_steps=1, max_goals=64,
                                     temperature_percent=150, num_premises=0),
                       policy_url, value_url, problems_sha256,
                       MODEL_CANONICAL_MANIFEST_SHA256)
        manifest_rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        solved_sessions = 0
        valid_unsolved_sessions = 0
        started_attempts = 0
        generated_tokens = 0
        lean_tactic_executions = 0
        rollout_rows = []
        for index, row in enumerate(manifest_rows, 1):
            sid = row["session_id"]
            problem_root = args.output_dir / sid
            attempts_root = problem_root / "attempts"
            attempts_root.mkdir(parents=True)
            terminal_attempt: Path | None = None
            attempt_rows = []
            solved = False
            for attempt_index in range(1, MAX_ATTEMPTS + 1):
                started_attempts += 1
                root = attempts_root / f"attempt_{attempt_index:02d}"
                root.mkdir()
                session = root / "session"
                session.mkdir()
                tree_id = f"{sid}-wave{args.wave_index:03d}-attempt{attempt_index:02d}"
                env = os.environ.copy()
                env.update({
                    "REAP_SESSION_ID": sid,
                    "REAP_SESSION_DIR": str(session),
                    "REAP_POLICY_ENDPOINT": policy_url.format(session_id=sid),
                    "REAP_VALUE_ENDPOINT": value_url.format(session_id=sid),
                    "REAP_PS_ENDPOINT": "",
                    "REAP_OBSERVER_PATH": str(root / "observer.jsonl"),
                    "REAP_TREE_ID": tree_id,
                    "REAP_POLICY_VERSION": str(args.wave_index),
                })
                one_manifest = root / "sessions.jsonl"
                one_manifest.write_text(
                    json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
                )
                receipt_directory = (
                    args.output_dir / "_actor_receipts" / sid / "policy_requests"
                )
                receipt_names_before = {
                    path.name for path in receipt_directory.glob("*.json")
                } if receipt_directory.is_dir() else set()
                started = time.monotonic()
                process = subprocess.Popen(
                    [str(args.lake), "env", "lean", row["theorem_file"]],
                    cwd=args.reap_project, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, start_new_session=(os.name != "nt")
                )
                try:
                    stdout, stderr = process.communicate(
                        timeout=args.per_session_timeout_seconds
                    )
                    timed_out = False
                except subprocess.TimeoutExpired:
                    terminate_process_group(process)
                    stdout, stderr = process.communicate()
                    timed_out = True
                (root / "lean.stdout.log").write_bytes(stdout)
                (root / "lean.stderr.log").write_bytes(stderr)
                raw_tree = save_observer_tree(
                    root / "observer.jsonl", root / "raw_tree.json"
                )
                source_receipts = sorted(
                    path for path in receipt_directory.glob("*.json")
                    if path.name not in receipt_names_before
                )
                if len(source_receipts) != 1:
                    raise RuntimeError(
                        f"{sid} attempt {attempt_index}: expected one new policy receipt, "
                        f"got {len(source_receipts)}"
                    )
                target = root / "actor_receipts" / sid / "policy_requests"
                target.mkdir(parents=True)
                os.link(source_receipts[0], target / source_receipts[0].name)
                receipt_value = json.loads(source_receipts[0].read_text(encoding="utf-8"))
                generated_tokens += int(
                    receipt_value.get("request_cost", {}).get("generated_tokens", 0)
                )
                result_path = session / "result.json"
                try:
                    search_result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    search_result = None
                result_bound = bool(
                    isinstance(search_result, dict)
                    and search_result.get("schema_version") == "reap.training.result.v1"
                    and search_result.get("session_id") == sid
                    and type(search_result.get("solved")) is bool
                    and type(search_result.get("status")) is str
                )
                solved = bool(
                    result_bound and search_result["solved"] is True
                    and search_result["status"] == "solved" and process.returncode == 0
                )
                valid_unsolved = bool(
                    result_bound and search_result["solved"] is False
                    and search_result["status"] == "exhausted"
                    and process.returncode != 0
                )
                evidence_complete = bool(
                    not timed_out and raw_tree.get("present")
                    and (solved or valid_unsolved)
                )
                result = (
                    "PASS" if solved else "VALID_UNSOLVED"
                    if valid_unsolved else "INFRASTRUCTURE_INCOMPLETE"
                )
                session_artifacts = {}
                for name in ("result.json", "progress.jsonl", "wall_clock.jsonl"):
                    path = session / name
                    if path.is_file():
                        session_artifacts[name] = file_record(path)
                if (root / "observer.jsonl").is_file():
                    for line in (root / "observer.jsonl").read_text(
                            encoding="utf-8").splitlines():
                        event = json.loads(line)
                        if event.get("kind") == "canonical_candidate_result":
                            lean_tactic_executions += int(
                                event.get("lean_tactic_executions", 0)
                            )
                report = {
                    "schema_version": "fate.policy_service.real_reap_e2e_smoke.v1",
                    "scope": "formal_wave_independent_one_step_attempt_no_training",
                    "session_id": sid,
                    "result": result,
                    "pins": {
                        "actor_config_sha256": actor_config_sha,
                        "adapter_name": adapter_alias,
                        "adapter_file_sha256": adapter_file_sha,
                        "adapter_behavior_state_sha256": behavior_sha,
                        "problems_sha256": problems_sha256,
                        "plan_sha256": sha256_file(args.plan),
                        "root_state_pins_sha256": sha256_file(args.root_state_pins),
                        "reap_root_state_sha256": pins[sid]["root_state_sha256"],
                    },
                    "settings": {
                        "generation": asdict(GENERATION),
                        "lean_max_steps": 1,
                        "max_attempts_per_problem": MAX_ATTEMPTS,
                        "attempt_index": attempt_index,
                        "wave_index": args.wave_index,
                        "training": False,
                        "retrieval": False,
                    },
                    "artifacts": {
                        "manifest": file_record(one_manifest),
                        "raw_tree": raw_tree,
                        "observer": file_record(root / "observer.jsonl"),
                        "policy_receipts": [
                            file_record(target / source_receipts[0].name)
                        ],
                        "session": session_artifacts,
                    },
                    "lean_returncode": process.returncode,
                    "lean_timed_out": timed_out,
                    "search_outcome": (
                        "solved" if solved else "exhausted"
                        if valid_unsolved else "invalid"
                    ),
                    "elapsed_seconds": time.monotonic() - started,
                }
                immutable_json(root / "report.json", report)
                terminal_name = "DONE.json" if evidence_complete else "FAILED.json"
                immutable_json(root / terminal_name, {
                    "state": "DONE" if evidence_complete else "FAILED",
                    "outcome": (
                        "solved" if solved else "exhausted"
                        if valid_unsolved else "infrastructure_error"
                    ),
                    "report_sha256": sha256_file(root / "report.json"),
                })
                attempt_rows.append({
                    "attempt_index": attempt_index,
                    "path": str(root),
                    "result": result,
                    "report_sha256": sha256_file(root / "report.json"),
                    "policy_receipt_sha256": sha256_file(source_receipts[0]),
                })
                print(json.dumps({
                    "event": "wave_attempt_complete", "session_id": sid,
                    "wave_index": args.wave_index, "attempt": attempt_index,
                    "max_attempts": MAX_ATTEMPTS, "problem": index,
                    "total_problems": SESSION_COUNT, "result": result,
                }), flush=True)
                if evidence_complete:
                    terminal_attempt = root
                if solved:
                    break
            if terminal_attempt is None:
                raise RuntimeError(f"{sid}: no complete Lean attempt")
            # Keep all attempts below attempts/, while exposing the selected
            # solved (or final valid exhausted) attempt at this root for the
            # existing collector and join command.
            hardlink_tree(terminal_attempt, problem_root)
            immutable_json(problem_root / "attempts.json", {
                "schema_version": "fate.formal_wave.problem_attempts.v1",
                "session_id": sid,
                "wave_index": args.wave_index,
                "max_attempts": MAX_ATTEMPTS,
                "attempts_started": len(attempt_rows),
                "selected_attempt": int(terminal_attempt.name.rsplit("_", 1)[1]),
                "stopping_reason": "strict_success" if solved else "attempt_cap",
                "attempts": attempt_rows,
            })
            rollout_rows.append({
                "session_id": sid,
                "wave_index": args.wave_index,
                "solved": solved,
                "attempts_started": len(attempt_rows),
                "problem_root": str(problem_root),
                "attempts_sha256": sha256_file(problem_root / "attempts.json"),
                "report_sha256": sha256_file(problem_root / "report.json"),
            })
            solved_sessions += int(solved)
            valid_unsolved_sessions += int(not solved)
            print(json.dumps({
                "event": "wave_session_complete", "session_id": sid,
                "completed": index, "total": SESSION_COUNT,
                "result": "PASS" if solved else "VALID_UNSOLVED",
            }), flush=True)
        rollout_manifest = args.output_dir / "rollout_manifest.jsonl"
        rollout_manifest.write_bytes(
            b"".join(canonical_bytes(item) + b"\n" for item in rollout_rows)
        )
        immutable_json(args.output_dir / "DONE.json", {
            "state": "DONE",
            "wave_index": args.wave_index,
            "sessions": SESSION_COUNT,
            "solved_sessions": solved_sessions,
            "valid_unsolved_sessions": valid_unsolved_sessions,
            "started_attempts": started_attempts,
            "max_attempts_per_problem": MAX_ATTEMPTS,
            "max_new_tokens_per_attempt": MAX_TOKENS,
            "generated_tokens": generated_tokens,
            "lean_tactic_executions": lean_tactic_executions,
            "rollout_manifest_sha256": sha256_file(rollout_manifest),
            "actor_config_sha256": actor_config_sha,
            "behavior_adapter_alias": adapter_alias,
            "adapter_file_sha256": adapter_file_sha,
            "adapter_behavior_state_sha256": behavior_sha,
        })
        return 0
    finally:
        for server, thread in ((policy_server, policy_thread), (value_server, value_thread)):
            if server is not None: server.shutdown(); server.server_close()
            if thread is not None: thread.join(timeout=5)
        model = base = None; gc.collect()
        try:
            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

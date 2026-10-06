#!/usr/bin/env python3
"""Capture 12 point-of-use Reap prompts and root states without loading a model.

Pinned Reap/Lean constructs every prompt.  A local recording endpoint returns
64 identical, provenance-bearing ``skip`` actions so the real generator path
can complete at most one search expansion.  No actor model, CUDA runtime,
optimizer, training, or synthetic Lean state is used.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
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
BRIDGE_ROOT = HERE.parents[1]
WORKSTREAM_ROOT = BRIDGE_ROOT.parent
sys.path.insert(0, str(BRIDGE_ROOT / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "shared_actor_bridge" / "src"))
sys.path.insert(0, str(WORKSTREAM_ROOT / "lean_integration" / "src"))

from policy_service_bridge import parse_reap_tactic_state  # noqa: E402
from policy_service_bridge.receipts import canonical_bytes, text_sha256  # noqa: E402
from fate_reap.session_builder import SearchOptions, write_sessions  # noqa: E402


ROUTE_RE = re.compile(
    r"^/sessions/([A-Za-z0-9_-]{1,96})/(policy|value)/v1/chat/completions$"
)
PROBE_ACTION = "skip"
POLICY_N = 64
POLICY_MAX_TOKENS = 256
POLICY_TEMPERATURE = 1.5
POLICY_TOP_P = 0.9


def _raise_termination(signum: int, _frame: object) -> None:
    raise InterruptedError(f"received termination signal {signum}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_problems_bytes(path: Path) -> bytes:
    """Read the canonical JSONL bytes from a raw or repository-sized gzip file."""
    payload = path.read_bytes()
    return gzip.decompress(payload) if path.suffix == ".gz" else payload


def immutable_json(path: Path, value: object) -> None:
    """Publish a fsync'd JSON file atomically and without overwrite semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_fsync(path: Path, value: Mapping[str, Any], lock: threading.Lock) -> None:
    data = canonical_bytes(dict(value)) + b"\n"
    with lock:
        with path.open("ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def resolve_problems_path(plan_path: Path, explicit: Path | None = None) -> Path:
    """Resolve the authoritative JSONL without binding to a host workspace path.

    Production bundles carry the file at the plan-declared path inside the
    current experiment.  ``explicit`` is the CLI override.  The final sibling
    fallback only supports this source checkout, whose data README deliberately
    references the immutable curriculum instead of duplicating it.
    """
    if explicit is not None:
        return explicit.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    relative = plan.get("source", {}).get("problems_relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("plan must declare a relative problems path")
    deployed = (plan_path.resolve().parent / relative).resolve()
    if deployed.is_file():
        return deployed
    compressed = deployed.with_name(f"{deployed.name}.gz")
    if compressed.is_file():
        return compressed
    development = (
        BRIDGE_ROOT.parents[2]
        / "fate_m_lean_curriculum_20x200_20261004"
        / "data"
        / "problems.jsonl"
    ).resolve()
    if development.is_file():
        return development
    raise FileNotFoundError(
        f"authoritative problems.jsonl missing at plan-relative path: {deployed}"
    )


def load_plan_and_tasks(plan_path: Path, problems_path: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != "fate.policy_service.representative_smoke_plan.v1":
        raise ValueError("unsupported representative smoke plan")
    source_bytes = read_problems_bytes(problems_path)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != plan.get("source", {}).get("problems_sha256"):
        raise ValueError("top-level problems.jsonl hash mismatch")
    records: dict[str, dict] = {}
    for line_number, line in enumerate(source_bytes.decode("utf-8").splitlines(), 1):
        if not line:
            continue
        row = json.loads(line)
        session_id = row.get("id")
        if not isinstance(session_id, str) or session_id in records:
            raise ValueError(f"invalid or duplicate problem id at line {line_number}")
        if text_sha256(str(row.get("formal_statement", ""))) != row.get("sha256"):
            raise ValueError(f"formal statement hash mismatch for {session_id}")
        records[session_id] = row
    if len(records) != plan.get("source", {}).get("required_record_count"):
        raise ValueError("problems.jsonl record count mismatch")
    selected: list[dict] = []
    seen: set[str] = set()
    for task in plan.get("tasks", []):
        session_id = task.get("session_id")
        if not isinstance(session_id, str) or session_id in seen or session_id not in records:
            raise ValueError("plan has a missing or duplicate session")
        row = records[session_id]
        if (
            row.get("family_index") != task.get("family_index")
            or row.get("variant_index") != task.get("variant_index")
            or row.get("sha256") != task.get("formal_statement_sha256")
        ):
            raise ValueError(f"plan/data binding mismatch for {session_id}")
        selected.append(row)
        seen.add(session_id)
    expected_count = plan.get("expected_session_count", 12)
    if (isinstance(expected_count, bool) or not isinstance(expected_count, int)
            or expected_count < 1 or len(selected) != expected_count):
        raise ValueError(
            f"capture requires exactly expected_session_count={expected_count} sessions"
        )
    return plan, selected


def search_options() -> SearchOptions:
    return SearchOptions(
        num_samples=POLICY_N,
        max_tokens=POLICY_MAX_TOKENS,
        max_steps=1,
        max_goals=64,
        temperature_percent=150,
        num_premises=0,
    )


def _validate_policy_request(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("policy request must be an object")
    allowed = {
        "model", "messages", "n", "temperature", "top_p", "max_tokens",
        "logprobs", "stream",
    }
    if set(value) - allowed or value.get("model") != "REAL-Prover":
        raise ValueError("policy request fields/model differ from the frozen contract")
    messages = value.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or set(messages[0]) != {"role", "content"}
        or messages[0].get("role") != "user"
        or not isinstance(messages[0].get("content"), str)
        or not messages[0]["content"]
    ):
        raise ValueError("policy request must contain one non-empty user prompt")
    expected = {
        "n": POLICY_N,
        "temperature": POLICY_TEMPERATURE,
        "max_tokens": POLICY_MAX_TOKENS,
        "logprobs": True,
    }
    for key, wanted in expected.items():
        actual = value.get(key)
        if isinstance(wanted, float):
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), wanted, rel_tol=0, abs_tol=0
            ):
                raise ValueError(f"policy {key} differs from the frozen contract")
        elif actual != wanted:
            raise ValueError(f"policy {key} differs from the frozen contract")
    if "top_p" in value and value["top_p"] != POLICY_TOP_P:
        raise ValueError("policy top_p differs from the frozen contract")
    if value.get("stream", False) is not False:
        raise ValueError("streaming policy requests are forbidden")
    return messages[0]["content"]


def controlled_policy_response(session_id: str) -> dict[str, Any]:
    choices = []
    token = {
        "token": PROBE_ACTION,
        "bytes": list(PROBE_ACTION.encode("utf-8")),
        "logprob": -1.0,
    }
    for index in range(POLICY_N):
        candidate_sha = hashlib.sha256(
            f"reap-prompt-capture\0{session_id}\0{index}".encode("utf-8")
        ).hexdigest()
        choices.append(
            {
                "index": index,
                "raw_sample_index": index,
                "service_candidate_sha256": candidate_sha,
                "message": {"role": "assistant", "content": PROBE_ACTION},
                "finish_reason": "stop",
                "logprobs": {"content": [token]},
            }
        )
    return {
        "id": f"prompt-capture-{session_id}",
        "object": "chat.completion",
        "created": 0,
        "model": "REAL-Prover",
        "choices": choices,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": POLICY_N,
            "total_tokens": POLICY_N,
        },
    }


def controlled_value_response(session_id: str) -> dict[str, Any]:
    return {
        "id": f"value-capture-{session_id}",
        "object": "chat.completion",
        "created": 0,
        "model": "REAL-Prover",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"score":0}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
    }


class CaptureStore:
    def __init__(self, output_root: Path, tasks: list[dict]):
        self.output_root = output_root.resolve()
        self.tasks = {str(task["id"]): task for task in tasks}
        self.captures: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def capture_policy(self, session_id: str, raw_body: bytes) -> tuple[dict[str, Any], Path]:
        if session_id not in self.tasks:
            raise ValueError("policy request uses a session outside the frozen task set")
        request = json.loads(raw_body)
        incoming_prompt = _validate_policy_request(request)
        root_state = parse_reap_tactic_state(incoming_prompt)
        response = controlled_policy_response(session_id)
        record = {
            "schema_version": "fate.reap_prompt_capture.v1",
            "session_id": session_id,
            "family_index": int(self.tasks[session_id]["family_index"]),
            "variant_index": int(self.tasks[session_id]["variant_index"]),
            "formal_statement_sha256": self.tasks[session_id]["sha256"],
            "incoming_prompt": incoming_prompt,
            "incoming_prompt_sha256": text_sha256(incoming_prompt),
            "root_state": root_state,
            "root_state_sha256": text_sha256(root_state),
            "raw_request_sha256": hashlib.sha256(raw_body).hexdigest(),
            "response_id": response["id"],
            "response_sha256": hashlib.sha256(canonical_bytes(response)).hexdigest(),
            "probe_action": PROBE_ACTION,
            "choice_count": POLICY_N,
            "model_loaded": False,
            "training": False,
        }
        path = self.output_root / "sessions" / session_id / "prompt_capture.json"
        with self.lock:
            if session_id in self.captures or path.exists():
                raise ValueError("duplicate policy prompt capture")
            immutable_json(path, record)
            self.captures[session_id] = record
        return response, path

    def record_value(self, session_id: str, raw_body: bytes) -> dict[str, Any]:
        if session_id not in self.tasks:
            raise ValueError("value request uses a session outside the frozen task set")
        request = json.loads(raw_body)
        if not isinstance(request, dict) or request.get("n") != 1:
            raise ValueError("expected Reap's one-choice value request")
        event = {
            "session_id": session_id,
            "raw_request_sha256": hashlib.sha256(raw_body).hexdigest(),
            "model": request.get("model"),
            "n": 1,
        }
        path = self.output_root / "sessions" / session_id / "value_requests.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        append_fsync(path, event, self.lock)
        return controlled_value_response(session_id)


class CaptureHandler(BaseHTTPRequestHandler):
    server: "CaptureServer"

    def log_message(self, fmt: str, *args: object) -> None:
        self.server.emit({"event": "capture_http", "message": fmt % args})

    def _send(self, status: int, value: object) -> None:
        body = canonical_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ok": True, "gpu_required": False}) if self.path == "/health" else self._send(
            404, {"error": "not_found"}
        )

    def do_POST(self) -> None:  # noqa: N802
        match = ROUTE_RE.fullmatch(self.path)
        if match is None:
            self._send(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8 * 1024 * 1024:
                raise ValueError("invalid request size")
            raw_body = self.rfile.read(length)
            session_id, kind = match.groups()
            if kind == "policy":
                response, path = self.server.store.capture_policy(session_id, raw_body)
                self.server.emit(
                    {
                        "event": "prompt_captured",
                        "session_id": session_id,
                        "capture_path": str(path),
                        "root_state_sha256": self.server.store.captures[session_id][
                            "root_state_sha256"
                        ],
                    }
                )
            else:
                response = self.server.store.record_value(session_id, raw_body)
            self._send(200, response)
        except Exception as exc:
            self.server.emit(
                {
                    "event": "capture_request_failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._send(400, {"error": "capture_request_failed", "type": type(exc).__name__})


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: CaptureStore, emit):
        self.store = store
        self.emit = emit
        super().__init__(address, CaptureHandler)


class Progress:
    def __init__(self, path: Path, total: int, total_timeout: float):
        self.path = path
        self.total = total
        self.total_timeout = total_timeout
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.completed = 0
        self.current_session: str | None = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_timeout - (time.monotonic() - self.started))

    def emit(self, value: Mapping[str, Any]) -> None:
        event = {
            **dict(value),
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "remaining_seconds": round(self.remaining, 3),
            "completed_sessions": self.completed,
            "total_sessions": self.total,
            "current_session": self.current_session,
            "gpu_required": False,
        }
        append_fsync(self.path, event, self.lock)
        print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)


def terminate_process_group(process: subprocess.Popen[Any]) -> None:
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


def run_lean_session(
    *,
    lake: Path,
    reap_project: Path,
    theorem: Path,
    session_id: str,
    session_dir: Path,
    policy_url: str,
    value_url: str,
    timeout_seconds: float,
    heartbeat_seconds: float,
    progress: Progress,
) -> dict[str, Any]:
    session_dir.mkdir(parents=True, exist_ok=True)
    observer = session_dir / "observer.jsonl"
    stdout_path = session_dir / "lean.stdout.log"
    stderr_path = session_dir / "lean.stderr.log"
    env = os.environ.copy()
    env.update(
        {
            "REAP_SESSION_ID": session_id,
            "REAP_SESSION_DIR": str(session_dir),
            "REAP_POLICY_ENDPOINT": policy_url,
            "REAP_VALUE_ENDPOINT": value_url,
            "REAP_PS_ENDPOINT": "",
            "REAP_OBSERVER_PATH": str(observer),
            "REAP_TREE_ID": f"{session_id}-prompt-capture",
            "REAP_POLICY_VERSION": "0",
        }
    )
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            [str(lake), "env", "lean", str(theorem)],
            cwd=reap_project,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=(os.name != "nt"),
        )
        next_heartbeat = time.monotonic() + heartbeat_seconds
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= timeout_seconds:
                    timed_out = True
                    terminate_process_group(process)
                    break
                if now >= next_heartbeat:
                    progress.emit({"event": "heartbeat", "stage": "run_real_reap_prompt_capture"})
                    next_heartbeat = now + heartbeat_seconds
                time.sleep(min(1.0, max(0.05, timeout_seconds - (now - started))))
        finally:
            terminate_process_group(process)
    result = {
        "schema_version": "fate.reap_prompt_capture.process.v1",
        "session_id": session_id,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "observer_sha256": sha256_file(observer) if observer.is_file() else None,
        "result_sha256": sha256_file(session_dir / "result.json")
        if (session_dir / "result.json").is_file()
        else None,
    }
    immutable_json(session_dir / "lean_process_result.json", result)
    return result


def build_pins_manifest(
    *,
    plan_path: Path,
    problems_path: Path,
    tasks: list[dict],
    store: CaptureStore,
    process_results: list[dict[str, Any]],
    reap_project: Path,
    lake: Path,
) -> dict[str, Any]:
    if set(store.captures) != {str(task["id"]) for task in tasks}:
        missing = sorted({str(task["id"]) for task in tasks} - set(store.captures))
        raise ValueError(f"not all frozen tasks produced exactly one prompt capture: {missing}")
    by_process = {str(item["session_id"]): item for item in process_results}
    if set(by_process) != set(store.captures) or any(item["timed_out"] for item in process_results):
        raise ValueError("process results are missing, duplicated, or timed out")
    pins = []
    for task in tasks:
        session_id = str(task["id"])
        capture = store.captures[session_id]
        capture_path = store.output_root / "sessions" / session_id / "prompt_capture.json"
        pins.append(
            {
                **capture,
                "capture_sha256": sha256_file(capture_path),
                "lean_process": by_process[session_id],
            }
        )
    sources = {}
    for name, path in {
        "entrypoint": HERE,
        "plan": plan_path,
        "problems": problems_path,
        "reap_generator": reap_project / "Reap" / "Tactic" / "Generator.lean",
        "reap_lake_manifest": reap_project / "lake-manifest.json",
        "reap_lean_toolchain": reap_project / "lean-toolchain",
        "lake": lake,
    }.items():
        if path.is_file():
            sources[name] = {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
    return {
        "schema_version": "fate.reap_prompt_root_state_pins.v1",
        "status": "COMPLETE",
        "capture_method": "real pinned Reap TacticGenerator.mkPrompt via one-expansion controlled skip probe",
        "gpu_used": False,
        "model_loaded": False,
        "training": False,
        "integration_bundle": False,
        "problems_sha256": sha256_file(problems_path),
        "plan_sha256": sha256_file(plan_path),
        "session_count": len(pins),
        "probe": {
            "action": PROBE_ACTION,
            "num_samples": POLICY_N,
            "max_tokens": POLICY_MAX_TOKENS,
            "max_steps": 1,
        },
        "pins": pins,
        "sources": sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--problems",
        type=Path,
        help="explicit authoritative JSONL; defaults to the path declared relative to --plan",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=BRIDGE_ROOT / "config" / "representative_smoke_plan.frozen.json",
    )
    parser.add_argument("--reap-project", type=Path, required=True)
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-session-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--total-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=20.0)
    args = parser.parse_args()
    try:
        args.problems = resolve_problems_path(args.plan, args.problems)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.output_dir.exists():
        parser.error("--output-dir must be new; prompt pins are immutable")
    if os.name != "nt":
        try:
            args.output_dir.resolve().relative_to(Path("/tmp").resolve())
        except ValueError:
            parser.error("--output-dir must be below /tmp")
    for path in (args.problems, args.plan, args.lake):
        if not path.is_file():
            parser.error(f"required file missing: {path}")
    if not args.reap_project.is_dir():
        parser.error(f"required Reap project missing: {args.reap_project}")
    if (
        args.per_session_timeout_seconds <= 0
        or args.total_timeout_seconds <= 0
        or not 1 <= args.heartbeat_seconds <= 30
    ):
        parser.error("timeouts must be positive and heartbeat must be 1..30 seconds")

    args.output_dir.mkdir(parents=True)
    state_path = args.output_dir / "STATE.json"
    atomic_json(state_path, {"state": "RUNNING", "gpu_used": False, "training": False})
    progress = Progress(
        args.output_dir / "progress.jsonl", total=12, total_timeout=args.total_timeout_seconds
    )
    server: CaptureServer | None = None
    server_thread: threading.Thread | None = None
    process_results: list[dict[str, Any]] = []
    previous_sigterm = signal.signal(signal.SIGTERM, _raise_termination)
    try:
        plan, tasks = load_plan_and_tasks(args.plan, args.problems)
        # The same real Reap capture path is also used by the frozen 40-row
        # held-out evaluation.  Keep progress denominators bound to the plan
        # instead of the original 12-row representative smoke.
        progress.total = len(tasks)
        store = CaptureStore(args.output_dir, tasks)
        server = CaptureServer(("127.0.0.1", 0), store, progress.emit)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        port = int(server.server_address[1])
        policy_base = f"http://127.0.0.1:{port}/sessions/{{session_id}}/policy/v1"
        value_base = f"http://127.0.0.1:{port}/sessions/{{session_id}}/value/v1"
        manifest_path = args.output_dir / "sessions.jsonl"
        write_sessions(
            tasks,
            args.output_dir / "theorems",
            manifest_path,
            search_options(),
            policy_base,
            value_base,
            sha256_file(args.problems),
            "7ffcbbcea4831ce54254a3514dd792d449587a397bcc3303165cdb611275ff84",
        )
        session_rows = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if [row["session_id"] for row in session_rows] != [task["id"] for task in tasks]:
            raise RuntimeError("session builder changed the frozen task order")
        progress.emit({"event": "phase", "stage": "real_reap_prompt_capture_ready"})
        for row in session_rows:
            if progress.remaining <= 0:
                raise TimeoutError("total prompt-capture budget exhausted")
            session_id = str(row["session_id"])
            progress.current_session = session_id
            progress.emit({"event": "phase", "stage": "run_real_reap_prompt_capture"})
            timeout = min(args.per_session_timeout_seconds, progress.remaining)
            result = run_lean_session(
                lake=args.lake,
                reap_project=args.reap_project,
                theorem=Path(row["theorem_file"]),
                session_id=session_id,
                session_dir=args.output_dir / "sessions" / session_id,
                policy_url=policy_base.format(session_id=session_id),
                value_url=value_base.format(session_id=session_id),
                timeout_seconds=timeout,
                heartbeat_seconds=args.heartbeat_seconds,
                progress=progress,
            )
            process_results.append(result)
            if result["timed_out"] or session_id not in store.captures:
                raise RuntimeError(f"session did not yield a bounded prompt capture: {session_id}")
            progress.completed += 1
            progress.emit(
                {
                    "event": "session_complete",
                    "stage": "prompt_captured",
                    "session_id": session_id,
                    "lean_returncode": result["returncode"],
                    "root_state_sha256": store.captures[session_id]["root_state_sha256"],
                }
            )
        pins = build_pins_manifest(
            plan_path=args.plan,
            problems_path=args.problems,
            tasks=tasks,
            store=store,
            process_results=process_results,
            reap_project=args.reap_project,
            lake=args.lake,
        )
        pins_path = args.output_dir / "root_state_pins.json"
        immutable_json(pins_path, pins)
        done = {
            "state": "DONE",
            "root_state_pins_sha256": sha256_file(pins_path),
            "session_count": len(tasks),
            "gpu_used": False,
            "training": False,
        }
        immutable_json(args.output_dir / "DONE.json", done)
        atomic_json(state_path, done)
        progress.current_session = None
        progress.emit({"event": "phase", "stage": "complete"})
        return 0
    except BaseException as exc:
        failure = {
            "state": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "gpu_used": False,
            "training": False,
        }
        immutable_json(args.output_dir / "FAILED.json", failure)
        atomic_json(state_path, failure)
        progress.emit({"event": "failed", "stage": "failed", "error_type": type(exc).__name__})
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

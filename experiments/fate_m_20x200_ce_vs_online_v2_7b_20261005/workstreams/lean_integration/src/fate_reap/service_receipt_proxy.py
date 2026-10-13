"""Session-aware OpenAI proxy that emits terminal policy/value receipts.

Point Reap at ``/sessions/{session_id}/policy/v1`` and
``/sessions/{session_id}/value/v1``.  The proxy reads the runner/coordinator's
atomic current_policy_state.json immediately before every upstream request.
"""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.request
import uuid


ROUTE = re.compile(r"^/sessions/([A-Za-z0-9_]+)/((?:policy)|(?:value))/v1/chat/completions$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WRITE_LOCK = threading.Lock()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _append(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with WRITE_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _state(root: Path, session_id: str) -> dict:
    value = json.loads((root / session_id / "current_policy_state.json").read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != "fate.policy_state.v1"
            or value.get("session_id") != session_id or type(value.get("tree_id")) is not str
            or type(value.get("step")) is not int or type(value.get("policy_version")) is not int):
        raise ValueError("invalid current_policy_state.json")
    return value


def _response_choices(body: bytes) -> tuple[str, int]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "error", 0
    if not isinstance(parsed, dict) or not isinstance(parsed.get("choices"), list):
        return "error", 0
    choices = parsed["choices"]
    valid = [choice for choice in choices if isinstance(choice, dict)
             and isinstance(choice.get("message"), dict)
             and isinstance(choice["message"].get("content"), str)
             and choice["message"]["content"].strip()]
    return ("ok", len(valid)) if valid else ("error", 0)


class Handler(BaseHTTPRequestHandler):
    server: "ReceiptServer"

    def log_message(self, fmt: str, *args: object) -> None:
        print(json.dumps({"event": "service_proxy", "message": fmt % args}), flush=True)

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, b'{"status":"ok"}') if self.path == "/health" else self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        match = ROUTE.match(self.path)
        if match is None:
            self._send(404, b'{"error":"invalid session service route"}')
            return
        session_id, service = match.groups()
        request_id = uuid.uuid4().hex
        raw_request = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        empty_hash = hashlib.sha256(b"").hexdigest()
        receipt = {
            "schema_version": "fate.service.request.v1", "request_id": request_id,
            "session_id": session_id, "service": service, "terminal": True,
            "model_sha256": self.server.model_sha256,
            "request_sha256": hashlib.sha256(raw_request).hexdigest(),
            "response_sha256": empty_hash, "http_status": None,
            "status": "error", "parse_status": "error", "choice_count": 0,
        }
        try:
            state = _state(self.server.receipt_root, session_id)
            receipt.update({"tree_id": state["tree_id"], "step": state["step"],
                            "policy_version": state["policy_version"]})
            payload = json.loads(raw_request)
            if not isinstance(payload, dict):
                raise ValueError("request JSON must be an object")
            if service == "policy":
                payload.update(self.server.generation)
            request_body = _canonical(payload)
            receipt["request_sha256"] = hashlib.sha256(request_body).hexdigest()
            upstream = self.server.policy_upstream if service == "policy" else self.server.value_upstream
            request = urllib.request.Request(
                upstream + "/chat/completions", data=request_body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.server.timeout) as response:
                    body, http_status = response.read(), int(response.status)
            except urllib.error.HTTPError as exc:
                body, http_status = exc.read(), int(exc.code)
            parse_status, choice_count = _response_choices(body)
            receipt.update({
                "response_sha256": hashlib.sha256(body).hexdigest(), "http_status": http_status,
                "parse_status": parse_status, "choice_count": choice_count,
                "status": "ok" if 200 <= http_status < 300 and parse_status == "ok" else "error",
            })
            _append(self.server.receipt_root / session_id / "service_requests.jsonl", receipt)
            self._send(http_status, body)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
            receipt.setdefault("tree_id", "")
            receipt.setdefault("step", -1)
            receipt.setdefault("policy_version", -1)
            receipt["error_type"] = type(exc).__name__
            _append(self.server.receipt_root / session_id / "service_requests.jsonl", receipt)
            self._send(502, _canonical({"error": "service proxy request failed", "request_id": request_id}))


class ReceiptServer(ThreadingHTTPServer):
    def __init__(self, address, *, policy_upstream: str, value_upstream: str,
                 receipt_root: Path, model_sha256: str, generation: dict, timeout: float):
        super().__init__(address, Handler)
        self.policy_upstream = policy_upstream.rstrip("/")
        self.value_upstream = value_upstream.rstrip("/")
        self.receipt_root = receipt_root.resolve()
        self.model_sha256 = model_sha256
        self.generation = generation
        self.timeout = timeout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-upstream", required=True)
    parser.add_argument("--value-upstream", required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args()
    if not SHA256_RE.fullmatch(args.model_sha256):
        parser.error("--model-sha256 must be 64 lowercase hex characters")
    generation = {"temperature": 1.5, "top_p": 0.9, "max_tokens": 256, "n": 64}
    server = ReceiptServer((args.host, args.port), policy_upstream=args.policy_upstream,
                           value_upstream=args.value_upstream, receipt_root=args.receipt_root,
                           model_sha256=args.model_sha256, generation=generation,
                           timeout=args.timeout_seconds)
    print(json.dumps({"event": "service_proxy_ready", "host": args.host, "port": args.port,
                      "generation": generation}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

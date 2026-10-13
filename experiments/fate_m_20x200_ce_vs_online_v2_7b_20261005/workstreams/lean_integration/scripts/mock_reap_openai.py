#!/usr/bin/env python3
"""Tiny deterministic OpenAI-shaped server for Reap wiring diagnostics only.

This is deliberately not a model or experiment result.  Requests with n=1
receive the value JSON used by Reap; larger requests receive repeated
``exact curriculum_target`` tactics.  It exists to capture the real Lean
observer/raw-tree shapes before connecting the 7B actor.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class Handler(BaseHTTPRequestHandler):
    server: "Server"

    def log_message(self, fmt: str, *args: object) -> None:
        print(json.dumps({"event": "mock_http", "message": fmt % args}), flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, canonical_bytes({"ok": True}))
        else:
            self._send(404, canonical_bytes({"error": "not_found"}))

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            request = json.loads(raw)
            n = int(request.get("n", 0))
            if n < 1:
                raise ValueError("n must be positive")
            self.server.request_count += 1
            if n == 1:
                texts = ['{"score":0}']
            else:
                texts = ["exact curriculum_target"] * n
            choices = []
            for index, text in enumerate(texts):
                choices.append({
                    "index": index,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                    "logprobs": {"content": [{
                        "token": text,
                        "bytes": list(text.encode()),
                        "logprob": -0.25,
                    }]},
                })
            response = {
                "id": f"mock-{self.server.request_count:08d}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.get("model", "REAL-Prover"),
                "choices": choices,
                "usage": {"prompt_tokens": 1, "completion_tokens": len(texts),
                          "total_tokens": 1 + len(texts)},
            }
            print(json.dumps({"event": "mock_request", "request_count": self.server.request_count,
                              "n": n, "path": self.path}, sort_keys=True), flush=True)
            self._send(200, canonical_bytes(response))
        except Exception as exc:  # diagnostic server; expose only error type
            self._send(400, canonical_bytes({"error": type(exc).__name__}))

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_count = 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = Server((args.host, args.port), Handler)
    print(json.dumps({"event": "mock_ready", "host": args.host, "port": args.port}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""``/eval`` 契约的 stdlib HTTP 服务（无额外依赖）。

契约（docs/new-python-version-mcts-plan/02 §5）::

    POST /eval
      {"prompt": "...", "k": 6, "temperature": 1.0,
       "policy_version": "v123", "request_id": "..."}
    → 200 {"candidates": [{"tactic": "...", "logprob": -3.21}, ...],
           "value": {"v": -7.4, "d_hat": 7.4},
           "model_revision": "REAL-Prover-fe76f68d", "adapter": "..."}

认证：``Authorization: Bearer <token>``；token 从 ``--token-file`` 读取（600 权限），
只驻内存、不进日志。未配置 token 时不做鉴权（仅建议本机/隧道场景）。

另有 ``GET /health``（不鉴权）：返回引擎状态，便于存活探针。
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

LOG = logging.getLogger("gpu_serve.serve")

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_K = 256


class EvalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler_cls, engine, token: Optional[str] = None):
        super().__init__(address, handler_cls)
        self.engine = engine
        self.token = token or None


def make_server(engine, host: str = "127.0.0.1", port: int = 18080,
                token: Optional[str] = None) -> EvalHTTPServer:
    return EvalHTTPServer((host, int(port)), EvalHandler, engine, token)


class EvalHandler(BaseHTTPRequestHandler):
    server_version = "gpu-serve/0.1"
    protocol_version = "HTTP/1.0"  # 简单稳妥：每请求独立连接

    # ---------------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802 (stdlib 命名)
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, self.server.engine.health())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/eval":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send(400, {"error": f"bad request: {exc}"})
            return
        try:
            prompt = payload["prompt"]
            if not isinstance(prompt, str):
                raise ValueError("'prompt' must be a string")
            k = int(payload.get("k", 6))
            if not 1 <= k <= MAX_K:
                raise ValueError(f"'k' must be in [1, {MAX_K}]")
            temperature = float(payload.get("temperature", 1.0))
        except (KeyError, TypeError, ValueError) as exc:
            self._send(400, {"error": f"bad request: {exc}"})
            return
        try:
            result = self.server.engine.eval(prompt, k, temperature)
        except Exception as exc:  # 服务端错误：日志留痕（不含请求头/token），500 返回
            LOG.exception("engine eval failed: %r", exc)
            self._send(500, {"error": f"engine failure: {type(exc).__name__}"})
            return
        if isinstance(result, dict):
            if payload.get("request_id") is not None:
                result.setdefault("request_id", payload["request_id"])
        self._send(200, result)

    # ---------------------------------------------------------------- #
    def _authorized(self) -> bool:
        token = self.server.token
        if not token:
            return True
        header = self.headers.get("Authorization", "") or ""
        return hmac.compare_digest(header, f"Bearer {token}")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            raise ValueError("empty body")
        if length > MAX_BODY_BYTES:
            raise ValueError("body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 (stdlib 签名)
        LOG.info("%s %s", self.address_string(), fmt % args)


def read_token_file(path: str) -> str:
    """读取 Bearer token；空文件视为配置错误。绝不回显内容。"""
    with open(path, "r", encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        raise SystemExit(f"--token-file {path} 为空")
    return token


def serve_forever(server: EvalHTTPServer) -> None:
    LOG.info("serving /eval on %s:%s", *server.server_address[:2])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("interrupted; shutting down")
    finally:
        server.server_close()

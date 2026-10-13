#!/usr/bin/env python3
"""脚本化 policy/value 服务（测试夹具，不是学习组件）。

用途：让真实 Lean 内核 + 对齐 MCTS 在不接 GPU 的情况下完成一次**可验证的求解**，
以验证 solved 路径（proof_script 抽取 / final check / replay check / 观察器）。

- policy 请求（路径以 /chat/completions 结尾且不含 /value/）：返回固定 tactic（n 份）；
- value 请求（路径含 /value/）：返回 {"score": ...}（搜索用 V=-score）。

示例：
    python3 scripts/scripted_policy_server.py --port 18081 \
        --tactic "intro x y h; simp only [step]; nlinarith [h]" --score -1.0
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    tactic = "skip"
    score = 0.0

    def log_message(self, *_args) -> None:
        return

    def send_json(self, code: int, obj: object) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": self.path})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/chat/completions"):
            if "/value/" in self.path:
                contents = [json.dumps({"score": self.score})]
            else:
                n = max(1, int(request.get("n", 1)))
                contents = [self.tactic] * n
            choices = [
                {
                    "index": index,
                    "message": {"role": "assistant", "content": content},
                    "logprobs": {"content": [{"token": content, "logprob": -0.01}]},
                }
                for index, content in enumerate(contents)
            ]
            self.send_json(200, {"id": "scripted", "choices": choices})
        else:
            self.send_json(404, {"error": self.path})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--tactic", default="skip")
    parser.add_argument("--score", type=float, default=-1.0)
    args = parser.parse_args()
    Handler.tactic = args.tactic
    Handler.score = args.score
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

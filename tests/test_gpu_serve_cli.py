"""gpu-serve CLI 测试（CPU）。

策略：重依赖按需 ``pytest.importorskip``，保证
- 无 torch 环境：--help / versions / mock 引擎 / 参考客户端契约测试仍全跑；
- 有 torch 环境：tiny 全回路（smoke / train offline / eval heldout / serve-eval tiny）全跑。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gpu_serve.cli import main as cli_main  # noqa: E402

# 参考实现（另一仓库）：在 my-new-linux 上存在；不存在则跳过相关契约测试
REFERENCE_REPO = "/mnt/gloway/projects/lean-mcts-cpu-server"


# --------------------------------------------------------------------------- #
# HTTP 小工具（stdlib；与 lmc/eval_client.py 同契约）
# --------------------------------------------------------------------------- #
def _post(url: str, payload: dict, token: str | None = None, timeout: float = 10.0):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _serve_in_thread(engine, token: str | None = None):
    from gpu_serve.serve import make_server

    server = make_server(engine, host="127.0.0.1", port=0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# --------------------------------------------------------------------------- #
# --help（无任何可选依赖）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv", [
    ["--help"],
    ["serve-eval", "--help"],
    ["train", "--help"],
    ["train", "offline", "--help"],
    ["train", "online", "--help"],
    ["eval", "--help"],
    ["eval", "heldout", "--help"],
    ["versions", "--help"],
    ["smoke", "--help"],
])
def test_cli_help_exits_zero(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_main(argv)
    assert excinfo.value.code == 0
    assert "gpu-serve" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# versions（无 torch 也须可跑）
# --------------------------------------------------------------------------- #
def test_versions_json(capsys):
    assert cli_main(["versions"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["gpu_serve"]
    assert "python" in info and "torch" in info


# --------------------------------------------------------------------------- #
# serve-eval：mock 引擎 + 契约 + token 鉴权（无 torch）
# --------------------------------------------------------------------------- #
def test_serve_eval_mock_contract_and_token():
    from gpu_serve.engines import MockEvalEngine

    token = "unit-test-token"
    engine = MockEvalEngine(model_revision="mock-rev", adapter="mock-lora")
    server, thread = _serve_in_thread(engine, token=token)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = _get(base + "/health")
        assert status == 200 and body["status"] == "ok"

        # 无 token → 401；不读 body 也能继续（HTTP/1.0 短连接）
        status, _ = _post(base + "/eval", {"prompt": "|- True", "k": 3, "temperature": 1.0})
        assert status == 401

        status, body = _post(base + "/eval",
                             {"prompt": "|- True", "k": 3, "temperature": 1.0,
                              "policy_version": "v-test", "request_id": "r-1"},
                             token=token)
        assert status == 200
        candidates = body["candidates"]
        assert len(candidates) == 3
        assert all(isinstance(c["tactic"], str) and isinstance(c["logprob"], float)
                   for c in candidates)
        value = body["value"]
        assert abs(value["v"] + value["d_hat"]) < 1e-9
        assert body["model_revision"] == "mock-rev"
        assert body["adapter"] == "mock-lora"
        assert body["request_id"] == "r-1"

        # 坏请求 → 400
        status, _ = _post(base + "/eval", {"prompt": "x", "k": 0}, token=token)
        assert status == 400
        status, _ = _post(base + "/eval", {"k": 2}, token=token)
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_serve_eval_reference_eval_client():
    """复用 lmc/eval_client.py（若该仓库在本机存在）验证真实客户端契约。"""
    if not os.path.isdir(REFERENCE_REPO):
        pytest.skip("参考仓库 lean-mcts-cpu-server 不在本机")
    if REFERENCE_REPO not in sys.path:
        sys.path.insert(0, REFERENCE_REPO)
    try:
        from lmc.eval_client import EvalClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"lmc.eval_client 不可导入: {exc}")

    from gpu_serve.engines import MockEvalEngine

    server, thread = _serve_in_thread(MockEvalEngine(model_revision="mock-ref"))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        client = EvalClient(base, timeout=10.0, retries=1)
        result = client.eval("|- False", k=4, temperature=0.7, request_id="ref-1")
        assert len(result.candidates) == 4
        assert abs(result.value + result.d_hat) < 1e-9
        assert result.model_revision == "mock-ref"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# tiny 全回路（需要 torch）
# --------------------------------------------------------------------------- #
def _make_pool(tmp_path, n_transitions: int = 4):
    from alphaproof.pipeline import make_failure_samples, result_to_trajectory, run_mock_search

    traj = result_to_trajectory(run_mock_search())
    transitions = list(traj.transitions)
    disproof, timeout = make_failure_samples()
    transitions += disproof.transitions + timeout.transitions
    transitions = transitions[:n_transitions]
    pool = tmp_path / "pool"
    pool.mkdir()
    lines = [json.dumps(t.to_dict(), ensure_ascii=False) for t in transitions]
    (pool / "shard-0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (pool / "manifest.json").write_text(
        json.dumps({"count": len(transitions)}, ensure_ascii=False), encoding="utf-8")
    return pool, transitions


def test_smoke_tiny_end_to_end(tmp_path):
    pytest.importorskip("torch")
    out = tmp_path / "smoke.json"
    assert cli_main(["smoke", "--tiny", "--device", "cpu", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["probe"]["forward_ok"] is True
    assert report["offline_updates"] and report["offline_updates"][0]["optimizer_steps"] == 1
    assert report["online_updates"]


def test_train_offline_tiny_and_heldout(tmp_path, capsys):
    pytest.importorskip("torch")
    pool, transitions = _make_pool(tmp_path)
    receipts_path = tmp_path / "receipts.jsonl"
    assert cli_main(["train", "offline", "--tiny", "--device", "cpu", "--pool", str(pool),
                     "--steps", "1", "--batch-size", "4", "--micro-batch-size", "2",
                     "--out", str(receipts_path), "--lr", "1e-3"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "train offline"
    assert report["pool"]["samples"] == len(transitions)
    receipts = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    assert len(receipts) == 1 and receipts[0]["optimizer_steps"] == 1

    heldout_out = tmp_path / "heldout.json"
    assert cli_main(["eval", "heldout", "--tiny", "--device", "cpu", "--pool", str(pool),
                     "--micro-batch-size", "2", "--out", str(heldout_out)]) == 0
    heldout = json.loads(heldout_out.read_text(encoding="utf-8"))
    metrics = heldout["metrics"]
    assert metrics["policy"]["n"] == len(transitions)
    assert metrics["policy"]["ce_mean"] is not None
    assert metrics["value"]["n"] >= 1
    assert metrics["value"]["d_hat_mae"] is not None


def test_train_offline_toml_precedence(tmp_path, capsys):
    pytest.importorskip("torch")
    pool, _ = _make_pool(tmp_path)
    cfg = tmp_path / "gpu.toml"
    cfg.write_text("[train]\nsteps = 1\nbatch_size = 2\nlr = 0.002\n", encoding="utf-8")
    assert cli_main(["train", "offline", "--tiny", "--device", "cpu", "--config", str(cfg),
                     "--pool", str(pool), "--lr", "0.003",
                     "--out", str(tmp_path / "r.jsonl")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hyper"]["lr"] == 0.003          # CLI 覆盖 TOML
    assert report["hyper"]["batch_size"] == 2      # TOML 覆盖默认
    assert report["hyper"]["steps"] == 1


def test_serve_eval_tiny_engine():
    pytest.importorskip("torch")
    from gpu_serve.config import ModelSetup
    from gpu_serve.engines import build_engine

    engine = build_engine(ModelSetup(tiny=True, device="cpu"), "tiny", max_new_tokens=4)
    server, thread = _serve_in_thread(engine)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = _post(base + "/eval", {"prompt": "|- True", "k": 2, "temperature": 1.0})
        assert status == 200
        assert len(body["candidates"]) == 2
        assert 1.0 <= body["value"]["d_hat"] <= 64.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

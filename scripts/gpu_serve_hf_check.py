#!/usr/bin/env python3
"""gpu_serve「真实 HF 装载路径」的 CPU 自检（云端离线时的替代验证）。

自建微型 GPT2 + 微型 64 桶头 + LoRA，覆盖与云端 7B 相同的代码路径：
- AutoModelForCausalLM / AutoTokenizer / dtype 关键字兼容层；
- value head 装载（hidden size 自适应、strict 校验）；
- HFEvalEngine.eval（transformers generate + logprob 汇总）；
- LoRA 两种挂载：目录式 PEFT adapter 与裸 state_dict（rank 推断）；
- train offline（HF encode_fn + OfflineLearner 1 步）、eval heldout；
- serve-eval 真实引擎的 HTTP 往返。

依赖：torch、transformers、peft、tokenizers（CPU 即可）。
用法（仓库根目录）::

    PYTHONPATH=. ~/lmc-run/venvs/torchcpu/bin/python scripts/gpu_serve_hf_check.py \
        [--work-dir /home/a/lmc-run/hf-path] [--gpu-serve-bin <console script>]

全部子步骤 rc=0 且输出末行 ``HF_PATH_CHECK: OK`` 即通过。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VOCAB = {
    "<pad>": 0, "<eos>": 1, "<unk>": 2,
    "|-": 3, "True": 4, "False": 5, "intro": 6, "B": 7, "exact": 8,
    "pell": 9, "aesop": 10, "split": 11, "goals": 12, "ring": 13, "simp": 14,
    "rw": 15, "Omega": 16, "5": 17, "+": 18, "7": 19, "CodexMathFive": 20, ".": 21,
}


def build_assets(run_dir: Path) -> dict:
    import torch
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    from alphaproof.net.value_head import ValueHead64

    base_dir = run_dir / "tiny-gpt2"
    head_path = run_dir / "value-head.pt"
    adapter_dir = run_dir / "lora-adapter"
    lora_raw = run_dir / "lora-raw.pt"
    pool_dir = run_dir / "pool"

    run_dir.mkdir(parents=True, exist_ok=True)
    base_dir.mkdir(parents=True, exist_ok=True)

    tokenizer = Tokenizer(WordLevel(vocab=VOCAB, unk_token="<unk>"))
    tokenizer.pre_tokenizer = Whitespace()
    fast = PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token="<unk>",
                                   pad_token="<pad>", eos_token="<eos>")
    fast.save_pretrained(base_dir)

    config = GPT2Config(vocab_size=len(VOCAB), n_positions=256, n_embd=32,
                        n_layer=2, n_head=2, bos_token_id=1, eos_token_id=1)
    GPT2LMHeadModel(config).save_pretrained(base_dir)
    torch.save(ValueHead64(hidden_size=32, mid=8, bins=64).state_dict(), head_path)

    from peft import LoraConfig, get_peft_model

    fresh = GPT2LMHeadModel(GPT2Config(vocab_size=len(VOCAB), n_positions=256,
                                       n_embd=32, n_layer=2, n_head=2))
    peft_model = get_peft_model(fresh, LoraConfig(
        r=4, lora_alpha=8, lora_dropout=0.0, target_modules=["c_attn"],
        bias="none", task_type="CAUSAL_LM"))
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)
    peft_model.save_pretrained(str(adapter_dir))
    raw = {k: v for k, v in peft_model.state_dict().items() if "lora_" in k}
    torch.save(raw, lora_raw)

    pool_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for prompt, action, value in [("|- True", "intro B", -2.0),
                                  ("|- False", "aesop", 0.0),
                                  ("|- ring", "ring", -3.0)]:
        lines.append(json.dumps({"prompt": prompt, "action": action,
                                 "value_target": value, "kind": "proof",
                                 "solved": True, "terminal_verified": True},
                                ensure_ascii=False))
    (pool_dir / "shard-0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (pool_dir / "manifest.json").write_text(json.dumps({"count": len(lines)}),
                                            encoding="utf-8")
    return {"vocab": len(VOCAB), "lora_raw_keys": len(raw)}


def _paths(run_dir: Path) -> dict:
    return {
        "base": str(run_dir / "tiny-gpt2"),
        "head": str(run_dir / "value-head.pt"),
        "adapter": str(run_dir / "lora-adapter"),
        "lora_raw": str(run_dir / "lora-raw.pt"),
        "pool": str(run_dir / "pool"),
    }


def run_cmd(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = proc.stdout.strip()
    try:
        parsed = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        parsed = {"raw_tail": out[-300:]}
    return {"rc": proc.returncode, "report": parsed, "stderr_tail": proc.stderr[-300:]}


def serve_roundtrip(paths: dict) -> dict:
    from gpu_serve.config import ModelSetup
    from gpu_serve.engines import build_engine
    from gpu_serve.serve import make_server

    engine = build_engine(
        ModelSetup(base=paths["base"], head=paths["head"], value_mid=8, device="cpu",
                   strict_head=True),
        "hf", max_new_tokens=6)
    server = make_server(engine, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"prompt": "|- True", "k": 3, "temperature": 1.0}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/eval", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=180) as resp:
            body = json.loads(resp.read().decode())
        return {"status": resp.status, "candidates": len(body["candidates"]),
                "d_hat": body["value"]["d_hat"],
                "sample_logprob": body["candidates"][0]["logprob"]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def trainable_check(paths: dict) -> dict:
    """训练命令的 LoRA 必须可训练（policy 优化器参数组非空）。"""
    from update_offline.learner import OfflineLearner

    from gpu_serve.components import load_components
    from gpu_serve.config import ModelSetup, TrainHyper

    loaded = load_components(
        ModelSetup(base=paths["base"], lora=paths["adapter"], head=paths["head"],
                   value_mid=8, device="cpu"),
        require_head=True, trainable_lora=True)
    lora_trainable = [n for n, p in loaded.model.named_parameters()
                      if "lora_" in n and p.requires_grad]
    learner = OfflineLearner(model=loaded.model, value_head=loaded.head,
                             encode_fn=loaded.encode_fn,
                             config=TrainHyper().to_train_config(), device="cpu")
    return {"lora_trainable_tensors": len(lora_trainable),
            "policy_optimizer_params": len(learner.optimizer.param_groups[0]["params"]),
            "lora_report": loaded.info.get("lora")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(Path.home() / "lmc-run" / "hf-path"))
    parser.add_argument("--gpu-serve-bin", default=None,
                        help="gpu-serve 可执行文件；默认用本 venv 的 `python -m gpu_serve`")
    args = parser.parse_args()
    run_dir = Path(args.work_dir)
    gpu_bin = ([args.gpu_serve_bin] if args.gpu_serve_bin
               else [sys.executable, "-m", "gpu_serve"])

    assets = build_assets(run_dir)
    paths = _paths(run_dir)
    common = ["--base", paths["base"], "--head", paths["head"],
              "--value-mid", "8", "--device", "cpu"]
    summary: dict = {"assets": assets, "trainable_check": trainable_check(paths)}
    summary["smoke_no_lora"] = run_cmd([*gpu_bin, "smoke", *common, "--skip-update"])
    summary["smoke_lora_dir"] = run_cmd(
        [*gpu_bin, "smoke", *common, "--lora", paths["adapter"], "--skip-update"])
    summary["smoke_lora_raw"] = run_cmd(
        [*gpu_bin, "smoke", *common, "--lora", paths["lora_raw"], "--rank", "4",
         "--lora-alpha", "8", "--lora-target-modules", "c_attn", "--skip-update"])
    summary["train_offline"] = run_cmd(
        [*gpu_bin, "train", "offline", *common, "--lora", paths["adapter"],
         "--pool", paths["pool"], "--steps", "1", "--batch-size", "2",
         "--micro-batch-size", "1", "--out", str(run_dir / "receipts.jsonl")])
    summary["eval_heldout"] = run_cmd(
        [*gpu_bin, "eval", "heldout", *common, "--lora", paths["adapter"],
         "--pool", paths["pool"], "--micro-batch-size", "1"])
    summary["serve_eval_hf"] = serve_roundtrip(paths)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = (summary["smoke_no_lora"]["rc"] == 0 and summary["smoke_lora_dir"]["rc"] == 0
          and summary["smoke_lora_raw"]["rc"] == 0 and summary["train_offline"]["rc"] == 0
          and summary["eval_heldout"]["rc"] == 0 and summary["serve_eval_hf"]["status"] == 200
          and summary["trainable_check"]["policy_optimizer_params"] > 0
          and summary["trainable_check"]["lora_trainable_tensors"] > 0)
    print("HF_PATH_CHECK:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

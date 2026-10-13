#!/usr/bin/env python3
"""云端真实模型冒烟：REAL-Prover 7B + LoRA + s18 64 桶头 + 两套更新。

在云 GPU（ROCm）上运行；预计数分钟（模型加载为主）。
    python3 scripts/cloud_real_smoke.py --model /mnt/workspace/models/REAL-Prover-fe76f68d \
        --head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
        --out /mnt/workspace/alphaproof-aligned/repo/outputs/cloud_smoke
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphaproof.config import TrainConfig  # noqa: E402
from alphaproof.net.value_head import ValueHead64, load_s18_head  # noqa: E402
from alphaproof.pipeline import make_failure_samples, result_to_trajectory, run_mock_search  # noqa: E402

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/mnt/workspace/models/REAL-Prover-fe76f68d")
    ap.add_argument("--head", default="/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt",
                    help="64 桶 7B 头（256→64）。备选："
                         "/mnt/workspace/new_value_head/artifact-fullv3/backend.full.pt；"
                         "注意 s18-d64-full205628/head/head.pt 实测为 2048→1 标量头，勿用。")
    ap.add_argument("--out", default="outputs/cloud_smoke")
    ap.add_argument("--skip-model", action="store_true", help="只做值头/小模型检查")
    args = ap.parse_args()

    import torch
    from alphaproof.tiny import build_tiny, tiny_encode_fn  # noqa: F401
    report = {"torch": torch.__version__, "hip": getattr(torch.version, "hip", None),
              "cuda_available": torch.cuda.is_available()}
    if torch.cuda.is_available():
        report["device"] = torch.cuda.get_device_name(0)

    # ---- 1. 64 桶头与 s18 权重 ----
    head = ValueHead64(hidden_size=3584, mid=256, bins=64)
    try:
        load_report = load_s18_head(head, args.head)
        report["s18_head"] = load_report
        with torch.no_grad():
            probe = head(torch.randn(2, 3584))
            report["head_forward_shape"] = list(probe.shape)
            report["head_decode"] = [float(x) for x in head.decode(probe)]
    except Exception as exc:  # 如实报告，不阻塞其他检查
        report["s18_head_error"] = repr(exc)

    # ---- 2. REAL-Prover 7B + LoRA（可选） ----
    if not args.skip_model:
        t0 = time.time()
        from transformers import AutoModelForCausalLM, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            args.model, local_files_only=True, torch_dtype=torch.bfloat16,
        )
        model = model.to("cuda" if torch.cuda.is_available() else "cpu")
        head = head.to(model.device)          # 值头必须与主干同设备
        report["model_hidden"] = int(model.config.hidden_size)
        report["model_vocab"] = int(model.config.vocab_size)
        try:
            from peft import LoraConfig, get_peft_model
            lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.02,
                              target_modules=TARGET_MODULES, bias="none",
                              task_type="CAUSAL_LM")
            model = get_peft_model(model, lora)
            report["lora"] = "applied"
        except Exception as exc:
            report["lora_error"] = repr(exc)
        model.config.use_cache = False
        report["model_load_seconds"] = round(time.time() - t0, 1)

        prompt = "|- CodexMathFive.Pell.Target"
        action = "intro B"
        enc = tokenizer(prompt + action, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True, return_dict=True)
            value_logits = head(out.hidden_states[-1][:, -1, :].float())
            report["value_decoded"] = [float(x) for x in head.decode(value_logits)]
        report["forward_ok"] = True

    # ---- 3. 两种更新（真实模型；无模型时退化为 tiny） ----
    result = run_mock_search()
    traj = result_to_trajectory(result)
    report["mock_search"] = result.summary()

    if args.skip_model:
        from alphaproof.tiny import build_tiny, tiny_encode_fn
        from update_offline.learner import OfflineLearner, make_hf_encode_fn
        from update_online.learner import OnlineLearner
        model, tok = build_tiny()
        t_head = ValueHead64(hidden_size=model.hidden_size)
        encode = tiny_encode_fn(tok)
    else:
        from update_offline.learner import OfflineLearner, make_hf_encode_fn
        from update_online.learner import OnlineLearner
        t_head = head
        encode = make_hf_encode_fn(tokenizer, device=str(model.device))

    cfg = TrainConfig(value_coef=1e-3, micro_batch_size=2, learn_batch_size=2)
    off = OfflineLearner(model=model, value_head=t_head, encode_fn=encode, config=cfg,
                         device=str(next(model.parameters()).device))
    disproof, timeout = make_failure_samples()
    batch = list(traj.transitions) + list(disproof.transitions) + list(timeout.transitions)
    report["offline_update"] = off.update(batch[:4])   # 冒烟：最多 4 条，CPU/GPU 均可

    on = OnlineLearner(model=model, value_head=t_head, encode_fn=encode, config=cfg,
                       device=str(next(model.parameters()).device))
    # logp_old 由“更新前”的当前策略现场估算（真实系统由采样端提供）
    events = []
    for t in traj.transitions[:3]:
        input_ids, labels = encode(t.prompt, t.action)[:2]
        with torch.no_grad():
            o = model(input_ids=input_ids, labels=labels, use_cache=False, return_dict=True)
            nll = torch.nn.functional.cross_entropy(
                o.logits[:, :-1].reshape(-1, o.logits.size(-1)), labels[:, 1:].reshape(-1),
                ignore_index=-100, reduction="sum")
        from alphaproof.data.events import Transition
        events.append(Transition(prompt=t.prompt, action=t.action, value_target=t.value_target,
                                 kind="proof", solved=True, terminal_verified=True,
                                 reward=1.0, logprob_old=float(-nll.detach().cpu())))
    receipts = [r for r in (on.submit(e) for e in events) if r is not None]
    tail = on.flush()
    if tail:
        receipts.append(tail)
    report["online_updates"] = receipts

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[cloud_real_smoke] 报告写入 {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

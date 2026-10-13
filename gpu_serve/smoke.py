"""``smoke``：真实 / tiny 冒烟。

- ``--tiny``：无需任何 HF 权重（torch 即可）——tiny 模型 + 头 + mock 搜索轨迹，
  跑通「前向 + value 解码 + offline 1 步 + online 1 步」；
- 真实模式：``--base [+--lora] --head`` 加载 7B 级组件，做 1 次前向 + value 解码；
  给了 ``--pool`` 再跑 1 步 offline 更新（可用 ``--skip-update`` 只验证装载）。

失败不掩盖：异常写进报告的 ``error`` 字段并返回非零。
"""

from __future__ import annotations

import json
import logging
import platform
import time
from typing import Any, Optional

from .components import LoadedComponents, load_components
from .config import build_lora_spec, build_model_setup, build_train_hyper
from .jsonl import load_pool, write_json

LOG = logging.getLogger("gpu_serve.smoke")

DEFAULT_PROMPT = "|- CodexMathFive.Pell.Target"
DEFAULT_ACTION = "intro B"


def _tiny_components(setup) -> LoadedComponents:
    from alphaproof.net.value_head import ValueHead64
    from alphaproof.tiny import build_tiny, tiny_encode_fn

    device = "cpu" if (setup.device or "auto") == "auto" else setup.device
    model, tokenizer = build_tiny()
    model = model.to(device)
    head = ValueHead64(hidden_size=model.hidden_size).to(device)
    return LoadedComponents(
        model=model, tokenizer=tokenizer, head=head, device=device,
        hidden_size=int(model.hidden_size), model_revision="tiny", adapter=None,
        info={"tiny": True}, head_report={"hidden_size": int(model.hidden_size)},
        encode_fn=tiny_encode_fn(tokenizer, device=device), tiny=True,
    )


def _forward_probe(loaded: LoadedComponents, prompt: str, action: str) -> dict:
    """prompt+action 一次前向 → 末位 hidden → 64 桶头 → d_hat（与 cloud_real_smoke 同口径）。"""
    import torch

    encoded = loaded.encode_fn(prompt, action)
    input_ids, labels = encoded[0], encoded[1]
    with torch.no_grad():
        out = loaded.model(input_ids=input_ids,
                           attention_mask=encoded[3] if len(encoded) > 3 else None,
                           labels=labels, output_hidden_states=True, use_cache=False,
                           return_dict=True)
        hidden = out.hidden_states[-1][:, -1, :].float()
        d_hat = [float(x) for x in loaded.head.decode(loaded.head(hidden))]
    return {"forward_ok": True, "d_hat": d_hat[0] if d_hat else None}


def _tiny_offline_update(loaded: LoadedComponents, hyper, steps: int = 1) -> list:
    from alphaproof.pipeline import make_failure_samples, result_to_trajectory, run_mock_search
    from update_offline.learner import OfflineLearner
    from update_offline.replay import OfflineBatchBuilder

    result = run_mock_search()
    proof = result_to_trajectory(result)
    disproof, timeout = make_failure_samples()
    batch = list(proof.transitions) + list(disproof.transitions) + list(timeout.transitions)
    config = hyper.to_train_config()
    learner = OfflineLearner(model=loaded.model, value_head=loaded.head,
                             encode_fn=loaded.encode_fn, config=config, device=loaded.device)
    builder = OfflineBatchBuilder(config, seed=int(hyper.seed))
    receipts = []
    for _ in range(max(1, steps)):
        chunk = builder.build(list(batch), min(4, len(batch)), None)
        receipt = learner.update(chunk, weights=builder.weights(chunk))
        receipts.append(receipt)
    return receipts


def _tiny_online_update(loaded: LoadedComponents, hyper) -> list:
    import torch
    from alphaproof.data.events import Transition
    from alphaproof.pipeline import result_to_trajectory, run_mock_search
    from update_online.learner import OnlineLearner

    traj = result_to_trajectory(run_mock_search())
    events = []
    for t in traj.transitions[:3]:
        encoded = loaded.encode_fn(t.prompt, t.action)
        input_ids, labels = encoded[0], encoded[1]
        with torch.no_grad():
            out = loaded.model(input_ids=input_ids, labels=labels, use_cache=False,
                               return_dict=True)
            nll = torch.nn.functional.cross_entropy(
                out.logits[:, :-1].reshape(-1, out.logits.size(-1)),
                labels[:, 1:].reshape(-1), ignore_index=-100, reduction="sum")
        events.append(Transition(prompt=t.prompt, action=t.action,
                                 value_target=t.value_target, kind="proof", solved=True,
                                 terminal_verified=True, reward=1.0,
                                 logprob_old=float(-nll.detach().cpu())))
    config = hyper.to_train_config(learn_batch_size=1)
    learner = OnlineLearner(model=loaded.model, value_head=loaded.head,
                            encode_fn=loaded.encode_fn, config=config, device=loaded.device)
    receipts = []
    for event in events:
        receipt = learner.submit(event)
        if receipt is not None:
            receipts.append(receipt)
    tail = learner.flush()
    if tail is not None:
        receipts.append(tail)
    return receipts


def run_smoke(args, cfg) -> int:
    setup = build_model_setup(args, cfg)
    hyper = build_train_hyper(args, cfg)
    lora_spec = build_lora_spec(args, cfg)
    prompt = getattr(args, "prompt", None) or DEFAULT_PROMPT
    action = getattr(args, "action", None) or DEFAULT_ACTION

    report: dict[str, Any] = {
        "command": "smoke",
        "gpu_serve": __import__("gpu_serve").__version__,
        "python": platform.python_version(),
        "tiny": bool(setup.tiny),
    }
    try:
        import torch

        report["torch"] = torch.__version__
        report["hip"] = getattr(torch.version, "hip", None)
    except Exception as exc:  # noqa: BLE001
        report["torch_error"] = repr(exc)

    started = time.time()
    try:
        if setup.tiny:
            loaded = _tiny_components(setup)
            load_seconds = 0.0
        else:
            loaded = load_components(setup, require_head=True, lora_spec=lora_spec,
                                     trainable_lora=not getattr(args, "skip_update", False))
            load_seconds = time.time() - started
        # CUDA 探测只在已落到 GPU 时做（某些无 GPU 机器的 is_available 探测会挂起）
        report["cuda_available"] = bool(loaded.device.startswith("cuda"))
        if report["cuda_available"]:
            try:
                import torch as _torch

                report["device_name"] = _torch.cuda.get_device_name(0)
            except Exception as exc:  # noqa: BLE001
                report["device_name_error"] = repr(exc)
        report["load"] = {
            "seconds": round(load_seconds, 2),
            "device": loaded.device,
            "hidden_size": loaded.hidden_size,
            "model_revision": loaded.model_revision,
            "adapter": loaded.adapter,
            "head_report": loaded.head_report,
            "model_info": loaded.info,
        }
        report["probe"] = _forward_probe(loaded, prompt, action)

        if not getattr(args, "skip_update", False):
            if setup.tiny:
                report["offline_updates"] = _tiny_offline_update(loaded, hyper, steps=1)
                report["online_updates"] = _tiny_online_update(loaded, hyper)
            elif getattr(args, "pool", None):
                from update_offline.learner import OfflineLearner
                from update_offline.replay import OfflineBatchBuilder

                transitions = load_pool(args.pool)
                config = hyper.to_train_config()
                builder = OfflineBatchBuilder(config, seed=int(hyper.seed))
                learner = OfflineLearner(model=loaded.model, value_head=loaded.head,
                                         encode_fn=loaded.encode_fn, config=config,
                                         device=loaded.device)
                batch = builder.build(transitions, min(4, max(1, len(transitions))), None)
                report["offline_updates"] = [learner.update(batch, weights=builder.weights(batch))]
            else:
                report["offline_updates"] = []
                report["note"] = "真实模式未给 --pool：只验证装载与前向（CE 更新未跑）"
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 冒烟失败如实记录
        LOG.exception("smoke failed")
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_s"] = round(time.time() - started, 2)

    if getattr(args, "out", None):
        write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1

"""训练子命令：``train offline`` / ``train online``。

铁律（docs/architecture/core-and-branches.md）：
- **只调用** ``update_offline.OfflineLearner`` / ``update_online.OnlineLearner``；
- 不复制、不修改 CE 更新逻辑；超参经 ``TrainConfig`` 注入；
- base / LoRA / value head 全部经 ``ModelSetup`` 参数注入。

online 现状：本子命令是「参数化壳 + 事件回放」模式——从 ``--events`` JSONL
读取已带 ``logprob_old`` 的 Transition 事件，逐条喂给 ``OnlineLearner``。
常驻事件流（live 服务）依赖主线提供行为策略版本/事件通道，见 docs/gpu-serve.md TODO。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from .components import LoadedComponents, load_components
from .config import ModelSetup, TrainHyper, build_lora_spec, build_model_setup, build_train_hyper
from .jsonl import kind_counts, load_pool, write_json, write_jsonl

LOG = logging.getLogger("gpu_serve.train")


# --------------------------------------------------------------------------- #
# 保存训练产物（value head 必存；LoRA adapter 若为 PEFT 模型则另存）
# --------------------------------------------------------------------------- #
def save_components(save_dir: str, loaded: LoadedComponents,
                    extra: Optional[dict] = None) -> dict:
    import torch

    out = Path(save_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Any] = {}

    head_path = out / "value-head.pt"
    tmp = head_path.with_name(head_path.name + ".tmp")
    torch.save(loaded.head.state_dict(), tmp)
    os.replace(tmp, head_path)
    saved["value_head"] = str(head_path)

    try:
        from peft import PeftModel

        if isinstance(loaded.model, PeftModel):
            adapter_dir = out / "adapter"
            tmp_dir = out / ".adapter.tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            loaded.model.save_pretrained(str(tmp_dir))
            if adapter_dir.exists():
                shutil.rmtree(adapter_dir)
            os.replace(tmp_dir, adapter_dir)
            saved["adapter"] = str(adapter_dir)
    except ImportError:  # pragma: no cover - 无 peft 时跳过
        pass

    meta = {
        "model_revision": loaded.model_revision,
        "adapter": loaded.adapter,
        "device": loaded.device,
        "hidden_size": loaded.hidden_size,
        "head_report": loaded.head_report,
        "model_info": loaded.info,
        "saved": saved,
    }
    if extra:
        meta.update(extra)
    meta_path = out / "meta.json"
    tmp_meta = meta_path.with_name(meta_path.name + ".tmp")
    tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_meta, meta_path)
    saved["meta"] = str(meta_path)
    return saved


def _load(args, cfg, *, require_head: bool):
    setup = build_model_setup(args, cfg)
    lora_spec = build_lora_spec(args, cfg)
    return load_components(setup, require_head=require_head, lora_spec=lora_spec,
                           trainable_lora=True), setup


# --------------------------------------------------------------------------- #
# train offline
# --------------------------------------------------------------------------- #
def run_offline(args, cfg) -> int:
    from update_offline.learner import OfflineLearner
    from update_offline.replay import OfflineBatchBuilder

    loaded, setup = _load(args, cfg, require_head=True)
    hyper = build_train_hyper(args, cfg)
    replay = load_pool(args.pool)
    if not replay:
        raise SystemExit(f"pool 为空: {args.pool}")
    sft_pool = load_pool(args.sft_pool) if getattr(args, "sft_pool", None) else None

    train_config = hyper.to_train_config()
    builder = OfflineBatchBuilder(train_config, seed=int(hyper.seed))
    learner = OfflineLearner(model=loaded.model, value_head=loaded.head,
                             encode_fn=loaded.encode_fn, config=train_config,
                             device=loaded.device)

    started = time.time()
    receipts = []
    for step in range(max(1, int(hyper.steps))):
        batch = builder.build(replay, max(1, int(hyper.batch_size)), sft_pool)
        if not batch:
            raise SystemExit("batch 构造为空（检查 --batch-size / pool）")
        receipt = learner.update(batch, weights=builder.weights(batch))
        receipt["step"] = step
        receipts.append(receipt)
        LOG.info("offline step %d: loss=%.4f policy=%.4f value=%.4f samples=%d",
                 step, receipt["loss"], receipt["policy_loss"], receipt["value_loss"],
                 receipt["samples"])

    if getattr(args, "out", None):
        write_jsonl(args.out, receipts)

    report: dict[str, Any] = {
        "command": "train offline",
        "model": _model_summary(loaded, setup),
        "hyper": hyper.to_dict(),
        "pool": {"path": str(args.pool), "samples": len(replay), "kinds": kind_counts(replay),
                 "sft_samples": len(sft_pool) if sft_pool else 0},
        "receipts": receipts,
        "elapsed_s": round(time.time() - started, 2),
    }
    if getattr(args, "save_dir", None):
        report["saved"] = save_components(args.save_dir, loaded,
                                          extra={"command": "train offline",
                                                 "hyper": hyper.to_dict()})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- #
# train online（参数化壳 + 事件回放；live 流待主线）
# --------------------------------------------------------------------------- #
TODO_LIVE = ("live 在线事件流（行为策略版本 / 采样端 logprob 通道）属主线范围，"
             "本壳只支持 --events JSONL 回放")

def run_online(args, cfg) -> int:
    from update_online.learner import OnlineLearner

    loaded, setup = _load(args, cfg, require_head=True)
    hyper = build_train_hyper(args, cfg)
    events_path = getattr(args, "events", None)
    if not events_path:
        raise SystemExit(f"train online 需要 --events <events.jsonl>；{TODO_LIVE}")
    events = load_pool(events_path)
    if not events:
        raise SystemExit(f"events 为空: {events_path}")

    train_config = hyper.to_train_config(learn_batch_size=max(1, int(hyper.batch_size)))
    learner = OnlineLearner(model=loaded.model, value_head=loaded.head,
                            encode_fn=loaded.encode_fn, config=train_config,
                            device=loaded.device)

    started = time.time()
    receipts = []
    for index, event in enumerate(events):
        receipt = learner.submit(event)
        if receipt is not None:
            receipt["event_index"] = index
            receipts.append(receipt)
    tail = learner.flush()
    if tail is not None:
        tail["event_index"] = len(events) - 1
        receipts.append(tail)

    if getattr(args, "out", None):
        write_jsonl(args.out, receipts)

    report: dict[str, Any] = {
        "command": "train online",
        "mode": "replay-shell",
        "todo": TODO_LIVE,
        "model": _model_summary(loaded, setup),
        "hyper": hyper.to_dict(),
        "events": {"path": str(events_path), "samples": len(events),
                   "kinds": kind_counts(events)},
        "receipts": receipts,
        "elapsed_s": round(time.time() - started, 2),
    }
    if getattr(args, "save_dir", None):
        report["saved"] = save_components(args.save_dir, loaded,
                                          extra={"command": "train online (replay-shell)",
                                                 "hyper": hyper.to_dict()})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- #
def _model_summary(loaded: LoadedComponents, setup: ModelSetup) -> dict:
    return {
        "base": setup.base or ("tiny" if setup.tiny else ""),
        "lora": setup.lora or None,
        "head": setup.head or None,
        "device": loaded.device,
        "hidden_size": loaded.hidden_size,
        "model_revision": loaded.model_revision,
        "adapter": loaded.adapter,
        "head_report": loaded.head_report,
    }

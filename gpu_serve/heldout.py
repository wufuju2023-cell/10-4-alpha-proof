"""``eval heldout``：在留出池上评 policy CE / value CE / d_hat 误差。

只做前向度量，不反传、不更新；损失定义与训练一致（调用
``OfflineLearner._policy_ce`` 与 ``two_hot_target``，不复制算法）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, List, Optional

from alphaproof.data.events import Transition

from .components import LoadedComponents, load_components
from .config import build_lora_spec, build_model_setup
from .jsonl import kind_counts, load_pool

LOG = logging.getLogger("gpu_serve.heldout")


def evaluate_heldout(model, head, encode_fn, transitions: List[Transition],
                     micro_batch_size: int = 8) -> dict:
    """返回聚合指标 dict（缺失面为 None，不虚构数字）。"""
    import torch
    from alphaproof.net.value_head import two_hot_target
    from update_offline.learner import OfflineLearner

    model.eval()
    if head is not None:
        head.eval()

    policy_losses: List[float] = []
    value_ce: List[float] = []
    d_abs_err: List[float] = []
    bin_hits: List[int] = []
    n_value = 0
    kind_policy: dict[str, List[float]] = {}

    device = next(model.parameters()).device
    with torch.no_grad():
        for start in range(0, len(transitions), max(1, int(micro_batch_size))):
            for sample in transitions[start:start + max(1, int(micro_batch_size))]:
                encoded = encode_fn(sample.prompt, sample.action)
                input_ids, labels, prompt_len = encoded[0], encoded[1], encoded[2]
                attn = encoded[3] if len(encoded) > 3 else None
                out = model(input_ids=input_ids, attention_mask=attn, labels=labels,
                            output_hidden_states=True, use_cache=False, return_dict=True)
                policy_loss = float(OfflineLearner._policy_ce(out.logits, labels).detach().cpu())
                policy_losses.append(policy_loss)
                kind_policy.setdefault(sample.kind, []).append(policy_loss)

                if head is not None and sample.value_target is not None:
                    hidden = out.hidden_states[-1][:, prompt_len - 1, :].float()
                    pred = head(hidden).reshape(1, -1)
                    target = two_hot_target(-float(sample.value_target),
                                            bins=int(head.bins)).to(pred.device)
                    loss = torch.nn.functional.cross_entropy(pred, target)
                    d_hat = float(head.decode(pred)[0])
                    d_true = -float(sample.value_target)
                    value_ce.append(float(loss.detach().cpu()))
                    d_abs_err.append(abs(d_hat - d_true))
                    bin_hits.append(int(abs(d_hat - d_true) < 1.0))
                    n_value += 1

    def _mean(values: List[float]) -> Optional[float]:
        return round(sum(values) / len(values), 6) if values else None

    metrics: dict[str, Any] = {
        "n_total": len(transitions),
        "kinds": kind_counts(transitions),
        "policy": {
            "n": len(policy_losses),
            "ce_mean": _mean(policy_losses),
            "perplexity": (round(2.718281828459045 ** (sum(policy_losses) / len(policy_losses)), 6)
                           if policy_losses else None),
            "ce_by_kind": {kind: _mean(vals) for kind, vals in kind_policy.items()},
        },
        "value": {
            "n": n_value,
            "ce_mean": _mean(value_ce),
            "d_hat_mae": _mean(d_abs_err),
            "bin_acc_lt1": (round(sum(bin_hits) / len(bin_hits), 6) if bin_hits else None),
        },
    }
    return metrics


def run_heldout(args, cfg) -> int:
    setup = build_model_setup(args, cfg)
    lora_spec = build_lora_spec(args, cfg)
    policy_only = bool(getattr(args, "policy_only", False))
    loaded: LoadedComponents = load_components(setup, require_head=not policy_only,
                                               lora_spec=lora_spec)
    transitions = load_pool(args.pool)
    if not transitions:
        raise SystemExit(f"pool 为空: {args.pool}")

    started = time.time()
    metrics = evaluate_heldout(
        loaded.model, None if policy_only else loaded.head, loaded.encode_fn, transitions,
        micro_batch_size=int(getattr(args, "micro_batch_size", 8) or 8),
    )
    report = {
        "command": "eval heldout",
        "pool": str(args.pool),
        "model": {
            "base": setup.base or ("tiny" if setup.tiny else ""),
            "lora": setup.lora or None,
            "head": None if policy_only else (setup.head or None),
            "device": loaded.device,
            "hidden_size": loaded.hidden_size,
            "model_revision": loaded.model_revision,
            "adapter": loaded.adapter,
        },
        "metrics": metrics,
        "elapsed_s": round(time.time() - started, 2),
    }
    if getattr(args, "out", None):
        from .jsonl import write_json

        write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

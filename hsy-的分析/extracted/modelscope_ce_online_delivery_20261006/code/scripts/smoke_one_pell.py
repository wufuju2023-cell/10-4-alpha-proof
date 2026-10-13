#!/usr/bin/env python3
"""单道佩尔题（Mock 版）端到端冒烟：搜索 → 轨迹 → 两种更新。

- 纯 Python 部分（搜索/目标）在任何环境可跑；
- 更新部分需要 torch；无 torch 时打印提示并跳过（退出码仍为 0）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphaproof.config import SearchConfig, TrainConfig  # noqa: E402
from alphaproof.pipeline import (make_failure_samples, result_to_trajectory,  # noqa: E402
                                 run_mock_search)
from alphaproof.targets.value_targets import extract_transitions  # noqa: E402


def main() -> int:
    print("== 1. 对齐版 MCTS（Pell Mock，τ=200、c_AND=64、未访问惩罚=32）==")
    result = run_mock_search(SearchConfig())
    root = result.root
    print(f"   solved={result.solved}  nodes={result.nodes_created}  sims={result.simulations}")
    print(f"   best_script={result.best_script}")
    transitions = extract_transitions(root)
    print("   value targets（官方回溯）:")
    for prompt, action, value in transitions:
        print(f"     d={-value:>4.1f}  {action!r:<40} <= {prompt[:44]!r}")
    assert result.solved, "Mock 搜索未解出——对齐实现有回归"

    try:
        import torch  # noqa: F401
    except Exception:
        print("== 2. 更新部分跳过：当前环境没有 torch ==")
        print("   在 my-new-linux 的 .venv（pip install torch）或云端 ROCm 环境重跑本脚本即可。")
        return 0

    from alphaproof.net.value_head import ValueHead64
    from alphaproof.tiny import build_tiny, tiny_encode_fn
    from update_offline.learner import OfflineLearner
    from update_offline.replay import OfflineBatchBuilder
    from update_online.learner import OnlineLearner

    proof = result_to_trajectory(result)
    disproof, timeout = make_failure_samples()

    print("== 2. 离线专家迭代（CE 模式 + 反证样本；timeout 剔除）==")
    model, tok = build_tiny()
    encode = tiny_encode_fn(tok)
    cfg = TrainConfig(value_coef=1e-3, sft_mix=0.1, learn_batch_size=1)
    builder = OfflineBatchBuilder(cfg, seed=0)
    replay: list = []
    stats = builder.ingest([proof, disproof, timeout], replay)
    sft_pool = [t for t in proof.transitions]
    off_learner = OfflineLearner(model=model, value_head=ValueHead64(hidden_size=model.hidden_size),
                                 encode_fn=encode, config=cfg)
    batch = builder.build(replay, batch_size=max(len(replay), 4), sft_pool=sft_pool)
    receipt = off_learner.update(batch)
    print(f"   replay={stats}  batch={receipt['samples']} kinds={receipt['kind_counts']} "
          f"loss={receipt['loss']:.4f} value_coef={cfg.value_coef}")

    print("== 3. 在线更新（一个奖励驱动两参数；batch=1 与 batch=all 两种）==")
    events = proof.transitions
    for name, bs in (("batch=1", 1), ("batch=all", len(events))):
        on_learner = OnlineLearner(model=model, value_head=ValueHead64(hidden_size=model.hidden_size),
                                   encode_fn=encode, config=TrainConfig(learn_batch_size=bs,
                                                                        value_coef=1e-3))
        made = 0
        for t in events:
            import torch as _t
            with _t.no_grad():
                ids, labels, _plen, _attn = encode(t.prompt, t.action)
                out = model(input_ids=ids, labels=labels, use_cache=False, return_dict=True)
                nll_sum = _t.nn.functional.cross_entropy(
                    out.logits[:, :-1].reshape(-1, out.logits.size(-1)),
                    labels[:, 1:].reshape(-1), ignore_index=-100, reduction="sum")
            logp_old = float(-nll_sum.detach().cpu())
            from alphaproof.data.events import Transition
            r = on_learner.submit(Transition(prompt=t.prompt, action=t.action,
                                             value_target=t.value_target, kind="proof",
                                             solved=True, terminal_verified=True,
                                             reward=1.0, logprob_old=logp_old))
            if r is not None:
                made += 1
        tail = on_learner.flush()
        made += 1 if tail is not None else 0
        print(f"   {name}: 更新次数={made} update_index={on_learner.update_index} "
              f"last_loss={on_learner.last_receipt['loss']:.4f}")
        assert made >= 1

    print("== SMOKE OK ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

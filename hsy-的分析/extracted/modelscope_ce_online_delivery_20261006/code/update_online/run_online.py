"""在线更新（batch 可配）冒烟运行器。

用法（CPU 即可，需要 torch）：
    python -m update_online.run_online --batch-size 1   # 每条轨迹更新一次
    python -m update_online.run_online --batch-size 3   # 凑 3 条更新一次
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphaproof.config import TrainConfig  # noqa: E402
from alphaproof.data.events import Transition  # noqa: E402
from alphaproof.pipeline import result_to_trajectory, run_mock_search  # noqa: E402
from update_online.learner import OnlineLearner  # noqa: E402


def make_online_events(trajectory, reward: float = 1.0) -> list:
    """把成功轨迹转成在线事件（一个成功轨迹 → 若干 (s,a) 事件，同一标量奖励）。"""
    events = []
    for t in trajectory.transitions:
        events.append(
            Transition(
                prompt=t.prompt, action=t.action, value_target=t.value_target,
                kind="proof", solved=t.solved, terminal_verified=True,
                reward=reward, logprob_old=None,  # 入队前用当前策略补齐
            )
        )
    return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=1, help="learn_batch_size：几条事件更新一次")
    ap.add_argument("--value-coef", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    from alphaproof.net.value_head import ValueHead64
    from alphaproof.tiny import build_tiny, tiny_encode_fn

    model, tokenizer = build_tiny()
    encode = tiny_encode_fn(tokenizer)

    result = run_mock_search()
    traj = result_to_trajectory(result)
    events = make_online_events(traj)
    print(f"[online] mock 搜索：solved={result.solved} nodes={result.nodes_created} "
          f"事件数={len(events)}")

    # 采样时旧策略的 logprob（真实系统由 policy 服务返回；此处现场估一次）
    with_value = _fill_logprob_old(model, encode, events)

    cfg = TrainConfig(value_coef=args.value_coef, learn_batch_size=args.batch_size)
    learner = OnlineLearner(model=model, value_head=ValueHead64(hidden_size=model.hidden_size),
                            encode_fn=encode, config=cfg)
    receipts = []
    for event in events:
        receipt = learner.submit(event)
        if receipt is not None:
            receipts.append(receipt)
            print(f"[online] 更新 #{receipt['update_index']}: samples={receipt['samples']} "
                  f"loss={receipt['loss']:.4f} policy={receipt['policy_loss']:.4f} "
                  f"value={receipt['value_loss']:.4f}")
    tail = learner.flush()
    if tail is not None:
        receipts.append(tail)
        print(f"[online] flush 更新 #{tail['update_index']}: samples={tail['samples']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in receipts:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[online] 回执写入 {args.out}")
    return 0


def _fill_logprob_old(model, encode, events) -> list:
    """用当前策略估 logprob_old（真实系统里由采样端提供，此处仅为可跑的冒烟）。"""
    import torch
    for e in events:
        input_ids, labels = encode(e.prompt, e.action)[:2]
        with torch.no_grad():
            out = model(input_ids=input_ids, labels=labels, use_cache=False, return_dict=True)
            nll_sum = torch.nn.functional.cross_entropy(
                out.logits[:, :-1].reshape(-1, out.logits.size(-1)),
                labels[:, 1:].reshape(-1), ignore_index=-100, reduction="sum")
        e.logprob_old = float(-nll_sum.detach().cpu())
    return events


if __name__ == "__main__":
    raise SystemExit(main())

"""离线专家迭代冒烟运行器。

用法（CPU 即可，需要 torch）：
    python -m update_offline.run_expert_iteration --steps 1 --batch-size 8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alphaproof.config import TrainConfig  # noqa: E402
from alphaproof.data.events import Transition  # noqa: E402
from alphaproof.pipeline import make_failure_samples, result_to_trajectory, run_mock_search  # noqa: E402
from update_offline.learner import OfflineLearner  # noqa: E402
from update_offline.replay import OfflineBatchBuilder  # noqa: E402


def build_sft_pool(seed_transitions) -> list:
    """把部分成功样本转成 SFT 池（无 value target，只贡献 policy CE）。"""
    out = []
    for t in seed_transitions:
        out.append(Transition(prompt=t.prompt, action=t.action, value_target=None, kind="sft",
                              solved=t.solved, terminal_verified=False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--value-coef", type=float, default=1e-3)
    ap.add_argument("--sft-mix", type=float, default=0.1)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    from alphaproof.tiny import build_tiny, tiny_encode_fn
    from update_offline.learner import make_hf_encode_fn  # noqa: F401  (接口说明用)

    model, tokenizer = build_tiny()
    encode = tiny_encode_fn(tokenizer)

    result = run_mock_search()
    proof = result_to_trajectory(result)
    extra = make_failure_samples()
    print(f"[offline] mock 搜索：solved={result.solved} nodes={result.nodes_created} "
          f"script={result.best_script}")
    print(f"[offline] 成功轨迹样本数 = {len(proof.transitions)}")

    cfg = TrainConfig(value_coef=args.value_coef, sft_mix=args.sft_mix, policy_lr=args.lr,
                      learn_batch_size=args.batch_size)
    builder = OfflineBatchBuilder(cfg, seed=args.seed)
    replay: list = []
    stats = builder.ingest([proof, *extra], replay)
    print(f"[offline] replay 并入统计：{stats}（timeout 默认剔除）")
    sft_pool = build_sft_pool(proof.transitions)

    learner = OfflineLearner(model=model, value_head=build_value_head(model), encode_fn=encode,
                             config=cfg)
    receipts = []
    for step in range(args.steps):
        batch = builder.build(replay, args.batch_size, sft_pool)
        receipt = learner.update(batch)
        receipt["step"] = step
        receipts.append(receipt)
        print(f"[offline] step {step}: loss={receipt['loss']:.4f} "
              f"policy={receipt['policy_loss']:.4f} value={receipt['value_loss']:.4f} "
              f"kinds={receipt['kind_counts']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in receipts:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[offline] 回执写入 {args.out}")
    return 0


def build_value_head(model):
    from alphaproof.net.value_head import ValueHead64
    return ValueHead64(hidden_size=model.hidden_size)


if __name__ == "__main__":
    raise SystemExit(main())

# update_offline：官方式离线专家迭代（CE 模式）

> 对应 AlphaProof 主 RL：actor 搜索产生轨迹 → replay buffer → learner 批量训练。
> 与 `update_online/` 完全独立：这里只做“离线批量”，不做逐条在线更新。

## 目标函数（对齐官方）

```text
policy：CE( 搜索选中的动作 a* | s )        # 对一条 tactic 的所有 token 求平均
value ：CE64( d(s) 的 two-hot 分布 )       # d(s) = -value_target，终端 0，OR -1+子，AND min
total = policy + value_coef * value        # value_coef 默认 1e-3（官方 Table 6）
```

- 目标 `d(s)` 来自 `alphaproof.targets.compute_value_target`（对搜索树做最优路径回溯）；
- 训练样本 `(prompt=state, action=tactic, value_target)`：
  - **proof**：证明轨迹，全部训练；
  - **disproof**：反证轨迹，全部训练（官方“证明+反证双向”），权重 `disproof_weight` 可调；
  - **timeout**：默认剔除（官方伪代码：超时不进训练），`include_timeout=True` 可开；
  - **sft**：只贡献 policy 项（无 value target），按 `sft_mix=0.1` 混比（官方 Table 6）。
- batch：`--batch-size`（官方 4096；本机按显存与吞吐配置），micro 分块做梯度累积。

## 文件

| 文件 | 作用 |
| --- | --- |
| `learner.py` | `OfflineLearner.update(batch)`：一次 optimizer step；返回 loss/权重/计数回执 |
| `replay.py` | replay 并入规则（按 kind 过滤）+ `OfflineBatchBuilder`（replay/SFT 混比） |
| `run_expert_iteration.py` | 冒烟/最小闭环：Mock 搜索 → 轨迹 → 构建 batch → 训练 |

## 用法

```bash
# 冒烟（需要 torch；tiny 模型即可）
python -m update_offline.run_expert_iteration --batch-size 8 --steps 1 --value-coef 1e-3 \
    --sft-mix 0.1 --out receipts.jsonl

# 接入真实 7B（由调用方提供 model / value_head / tokenizer）
from update_offline.learner import OfflineLearner, make_hf_encode_fn
learner = OfflineLearner(model, value_head, make_hf_encode_fn(tokenizer), config)
receipt = learner.update(batch)     # batch: list[Transition]
```

## 与规模对齐的说明

官方 batch=4096、1M steps、60M replay、TPU 集群；本仓库**不复刻规模**。
`--batch-size` 语义与官方一致（一次更新覆盖的样本数），只是数量级按本机算力设定。
value 权重从历史的 0.5 切到 1e-3 后，需在 64 桶 CE 与归一化目标下重新校准（见 docs/alignment.md）。

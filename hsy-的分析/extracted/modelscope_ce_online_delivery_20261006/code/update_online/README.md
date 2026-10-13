# update_online：本机式在线更新（RTTT / TTTRL）

> 对应本机创新“一个奖励同时驱动两组参数”。与 `update_offline/` 独立：
> 这里不做大批量 CE 训练，而是**按可配置的 batch 数量收集已验证轨迹后立即更新**。

## 目标函数

```text
policy：-r·Δ + β_kl·Δ²          # Δ = logp_target - logprob_old；r 为标量奖励
value ：CE64( d(s) 的 two-hot )   # 显式 value target；**不再用 r 兜底**（修 G1）
total = mean(policy) + value_coef * mean(value)   # value_coef 默认 1e-3
```

- `r ∈ [-1, 1]`；**正奖励必须 `terminal_verified=true`**（内核/独立验证器确认）；
- `value_target` 缺省时不训练 value 项（而不是回退用 r）——对齐“价值=剩余步数”的语义；
- KL 锚：β_kl=0.05（本机防遗忘机制，官方无此机制，保留为 ➕）。

## batch 语义（用户核心需求）

| `learn_batch_size` | 行为 |
| --- | --- |
| `1`（默认） | 每收到 1 条已验证事件就执行一次更新 |
| `N` | 事件入队累计，凑满 N 条执行一次更新；`flush()` 处理残余 |

- 队列/计数在 learner 内部维护，回执含 `update_index / samples / optimizer_steps`；
- micro 分块（默认 ≤16/次 backward）支持大 batch 的梯度累积；
- 更新前自动快照 policy+value 参数，非有限损失自动回滚。

## 用法

```bash
# 冒烟：batch=1 与 batch=all 两种对照（需要 torch）
python -m update_online.run_online --batch-size 1
python -m update_online.run_online --batch-size 4

# 接入真实 7B
from update_online.learner import OnlineLearner, make_hf_encode_fn
learner = OnlineLearner(model, value_head, make_hf_encode_fn(tokenizer),
                        TrainConfig(learn_batch_size=8, value_coef=1e-3))
learner.submit(event)   # 达到 8 条时返回更新回执，否则 None
```

## 与官方 TTRL 的区别（重要）

- 官方 **TTRL**（论文）= 难题目**变体生成** + 聚焦 RL，是批量的第二阶段课程；
- 本机 **TTTRL/RTTT** = Test-Time Training on Rollouts，per-theorem 在线更新；
- 两者“不同名同构”，本目录实现的是后者；官方 TTRL 变体生成未实现（列为后续）。

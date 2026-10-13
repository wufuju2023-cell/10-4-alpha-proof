# Online-v2 实验臂

状态：**完整 raw-completion PPO receipt 路径和真实 7B 单次更新 CLI 已实现并通过 CPU 边界验证；真实 signed join 输出仍须在 GPU 主机执行。**

这是 FATE-M 20×200 CE 对照实验的 Online-v2 工作流。源码可独立测试，合并目标和真实
接线见 `INTEGRATION.md`，算法冻结项见 `config/online_v2_arm.frozen.json`。

## 相比旧在线实现的实质修正

- 不再用 `-rΔ + βΔ²` 把采样 action 的平方 log-ratio 冒充 KL。
- tactic 是一个序列 action；importance ratio 是所有 action token ratio 的乘积，再在
  sequence 层做 PPO clipping。
- advantage 是 `Q(s,a)-b(s)`；baseline 只比较同一 Lean state 的候选，并按其代表的
  raw draw multiplicity 加权。该字段是观测计数，不是理论 `q(x)`；单候选没有比较证据，
  advantage 为 0。
- PPO 每行对应一个不同的原始 token completion；完全相同的 completion 才能折叠，且
  `sample_multiplicity` 同时保留其 baseline 与 PPO 总权重。每行引用实际 Lean execution
  event，因此多个不同 raw completion 可共用同一执行结果而不重复调用 Lean。
- PPO action mask 覆盖完整生成 completion；EOS stop 行含终止 EOS，length-stop 行保留全部
  token 且不含 EOS。单一状态可混合两类行。tactic span 只用于执行来源追溯。
- behavior trust region 与 immutable base anchor 分开，二者均在 sampled prefix 上对
  完整词表计算 forward KL。
- old logprob 会用冻结 behavior snapshot 重算核对，防止 tokenizer/策略版本漂移。
- learner 只接受由不可变 search/verifier/proof-path 回执构造的 `ValidatedRolloutWave`；
  mask、tokenizer/EOS、完整候选集、advantage 和 value path 都会在训练入口重算/复核。
- batch 门槛按独立 problem；损失让每个 problem 总权重相等，并限制同轨迹事件数。
- wave 用 micro-batch 做梯度累积；behavior/base 逐块重新前向并立即压缩到 action context，
  不会保留整个 wave 的三份 `[B,L,V]` logits。
- accepted wave 通过跨进程锁、CAS 和 PREPARED/COMMITTED 两阶段事务同时封存 checkpoint、
  update receipt 与消费状态；崩溃会恢复明确的 before/committed checkpoint。
- search → verifier → proof-path 建立内容 SHA-256 链；同 ID 替换内容不能复用旧验证奖励。
- 单骨干 PEFT 三视图已有带 `RLock`、异常恢复、receipt behavior/base identity 绑定及
  live adapter/base hash 校验的实现。
- 正奖励必须有独立严格 Lean 终局回执；timeout/基础设施错误为 0，不反向污染整条轨迹。
- 与 CE 臂一致保留 `1e-3 × CE64` 剩余步数 value loss；只有严格验证路径有 value 标签。Online 内部保存正的 `value_distance=L-i`，它完全由 proof-path receipt 推导，对应 canonical `value_target=-(L-i)`；超过 64 步直接拒绝。
- verified path 的非终端 event 必须是 `unresolved`（tactic 成功推进但尚未闭合）；`invalid_tactic`、timeout 或基础设施错误不能混入路径取得 value 标签。底层 two-hot 编码对 `<1`、`>64` 和非有限值 fail closed，不做 clamp。
- 硬 KL、非有限数值、canary 失败会回滚 trainable parameters、optimizer、scheduler、
  RNG、step counter 和自适应 KL beta。快照不复制只读 7B backbone。

## 验证

在本目录运行：

```powershell
python -m pytest -q
python scripts/smoke.py
```

## 真实 REAL-Prover 7B 单次更新

`scripts/run_real_one_update.py` 是 join 输出到现有 `OnlineV2Learner` 的最短可执行
入口。它不复制 PPO/KL/value 算法。入口会：

- 分别校验 join 文件 SHA、signed canonical payload SHA 和 signed 文件 SHA；
- 从 JSON 递归恢复 reviewed receipt dataclass，并只通过 `build_rollout_samples`
  生成 `ValidatedRolloutWave`；
- 在分配 7B 前校验模型 lock、初始 LoRA、CE64 value head、目标仓库提交/源码、
  tokenizer 和 runtime pins；
- 用一个 PEFT backbone 加载独立 policy/behavior adapter，behavior 使用产生 receipt
  时的精确 adapter alias 和 actor hash framing；
- 保持 policy LM 为 eval mode（autograd 仍开启），避免 LoRA dropout 使初始
  `q_new != q_old`；
- 使用现有 PREPARED/COMMITTED/ABORTED ledger，实现进程崩溃恢复、拒绝回滚和
  已提交更新的幂等 resume；
- 原子导出 policy adapter、value head、update receipt、跨-wave `training_state.pt`
  （optimizer/scheduler/RNG/step counter/KL beta）和哈希 manifest，并每 20 秒输出 GPU 心跳。

优先让 preparer 从新 join 和当前本地资产直接计算全部哈希；不需要手工填写模板：

```bash
cd /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/online_v2_arm

python scripts/prepare_real_one_update_config.py \
  --joined /tmp/NEW-JOIN/online-v2-receipts.json \
  --signed-receipt /tmp/NEW-JOIN/strict-replay/receipt.json \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --adapter /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/assets/initial_lora_r16_a32_seed20261004 \
  --value-head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
  --target-repo /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/src/10-4-alpha-proof \
  --behavior-adapter-name fate_m_003_v001 \
  --output config/real_one_update.smoke.frozen.json

CONFIG=config/real_one_update.smoke.frozen.json
CONFIG_SHA=$(sha256sum "$CONFIG" | awk '{print $1}')

python scripts/run_real_one_update.py \
  --config "$CONFIG" \
  --expected-config-sha256 "$CONFIG_SHA" \
  --output /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runs/smoke/online-v2-real-one-update

# 对 RUNNING/FAILED 后的同一 config/input 做恢复；COMMITTED 更新不会再 step。
python scripts/run_real_one_update.py \
  --config "$CONFIG" \
  --expected-config-sha256 "$CONFIG_SHA" \
  --output /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runs/smoke/online-v2-real-one-update \
  --resume
```

`mode=smoke` 强制 `min_independent_problems=1` 且 `update_epochs=1`；它只用于当前
单题 signed-receipt 更新门禁。`mode=formal` 强制完整 20 问题 wave 和 2 epoch，且
`pins.receipts` 必须列出该 wave 的全部 join 文件。不同 Reap session 当前用不同
adapter alias，而 actor hash 包含 alias；在启动 20 题正式 wave 前，shared actor 必须
固定一个公共 behavior alias（或发布 alias-normalized hash schema）。CLI 会拒绝混合
identity，绝不会把单题 smoke 当成正式 wave。

正式 8-wave 运行中，wave 1 从冻结初始状态开始；wave 2–8 的 config 必须把前一 wave
`DONE.json.checkpoint.resume_training_state` 原样写入
`pins.resume_training_state`，同时把 adapter/value-head pins 指向前一 checkpoint 的对应
文件。入口会在本 wave optimizer step 前校验该文件 SHA 并恢复 optimizer、scheduler、
Python/NumPy/Torch CPU/CUDA RNG、累计 optimizer step 和自适应 KL beta；缺 pin 即拒绝
wave 2+。`--resume` 仍只表示同一 wave 的事务恢复，不替代这个跨-wave pin。

单测覆盖极端 sequence ratio 梯度、q_new=0 支持变化、完整词表 KL、receipt/hash-chain 篡改、完整 raw mask/EOS、raw draw multiplicity 加权
baseline、problem/value 权重、micro-batch 等价、wave 重放、单骨干 adapter 锁与 hash、严格
奖励、65 步/value-distance 溢出拒绝、失败非终端路径注入、old-logprob 版本错误、两实例竞争、commit-then-raise 及 PREPARED crash recovery。

## 目录

- `src/alphaproof_online_v2_arm/`：可合并实现。
- `tests/`：无网络、无大模型单元测试。
- `scripts/smoke.py`：合成 CPU 接线 smoke。
- `scripts/run_real_one_update.py`：真实 7B + PEFT、signed join 输入的单次更新入口。
- `config/real_one_update.smoke.template.json`：必须补齐哈希后另存并冻结的模板。
- `config/online_v2_arm.frozen.json`：算法协议；真实运行 sizing 参数仍明确标为待冻结。
- `runs/`：生成的 smoke 回执。

本工作流不声称 Online-v2 优于 CE；只有匹配预算、真实 Lean 留出集评测才能回答实验问题。

正式 20x10 wave 使用同一个配置生成入口：重复提供 20 对 `--joined` 和
`--signed-receipt`，并指定 `--mode formal`。入口会在加载 7B 前要求恰好 20 个独立
problem、统一的 wave/behavior/base identity，并冻结 `min_independent_problems=20`、
`update_epochs=2`。v009-v010 不得出现在这些训练 receipt 中。

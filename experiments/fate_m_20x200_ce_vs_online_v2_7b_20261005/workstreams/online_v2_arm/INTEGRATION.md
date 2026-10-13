# 目标仓库接线说明

## 合并位置

本工作流的 `src/alphaproof_online_v2_arm/` 是独立、可测试的实现。合并到
`10-4-alpha-proof` 时建议落在 `update_online_v2/`，不要覆盖现有
`update_online/`；后者的 `-rΔ + βΔ²` 只保留为 legacy 诊断臂。导入名可在合并时
由 `alphaproof_online_v2_arm` 机械替换为 `update_online_v2`。

需要同时扩展 `alphaproof.data.events.Transition` 的持久化 schema，使 actor 写出：

- `problem_id / trajectory_id / state_id / policy_version`，以及 behavior/base 的
  version 与内容 SHA-256；
- 每个 distinct raw completion 的 `input_ids=prompt+completion`、全 completion
  `action_mask`、temperature/top-p warped `q_old` logprobs 和 audit-only unwarped `p_old`；
- raw-row event ID、source Lean execution event ID、raw sample indices/multiplicity、原始
  finish reason，以及仅供执行来源追溯的 tactic span/action token IDs；
- `action_value / baseline_value / advantage`；其中 advantage 是按同 state 方差缩放并截断后
  的训练系数，原始 Q 与 baseline 仍保留供审计；
- search receipt 自身 SHA；verifier 必须绑定 search ID/SHA；proof path 必须绑定 terminal
  verifier ID/SHA 及有序 event/search/verifier 链摘要；
- `verifier_status / terminal_verified / verifier_receipt_id / local_reward`；
- `on_verified_solution_path / value_distance`；只有严格验证路径才有 CE64 剩余步数标签。

旧 schema 只有整条 tactic 的 `logprob_old`，不能校验 tokenization，也不足以稳定地
计算序列 ratio；不得在 learner 端用“当前模型现算的 logprob”冒充 rollout 时旧概率。

## 一次 wave 的顺序

1. 冻结当前 LoRA 为 `behavior_policy_version`，该版本完成整个 wave 的生成；中途不得
   更新 actor。
2. 每个已启动请求立即落盘，保存模型 revision、tokenizer hash、采样 seed 和逐 token
   old logprob。
3. Lean 严格验证生成独立回执。只有 proof/disproof 的终局回执能产生 `+1`；timeout、
   unresolved、基础设施错误均为 `0`，invalid tactic 的 `-0.1` 只属于当前 action。
4. builder 先将内容绑定的 verifier 结果写回 action return：严格 proof/disproof 为
   `+1`，invalid tactic 为 `-0.1`，timeout/infrastructure error 为 `0`；只有 unresolved
   action 保留 producer search Q。这样终局动作不会错误沿用执行前的 parent-state value。
   再对同一 state 的 raw draws 用 `compute_search_advantages` 形成 multiplicity 加权
   baseline；它对应每个原始采样 draw 的等权平均，不能再乘理论 `q(x)`。只有一个候选或
   effective Q 相同时 advantage 为零，且全 wave 都为零时 learner fail closed。
5. `build_rollout_samples` 验证三个不可变回执并生成 tamper-evident
   `ValidatedRolloutWave`；learner 拒绝松散 `RolloutSample`。`ProblemGroupedBuffer` 等待 20
   个独立题族，并限制单题、单轨迹事件数。learner 再用
   `problem_balanced_weights` 让每个题族的总损失权重相同。
6. learner 首先对完整 raw completion（stop 时包含 EOS）重算 temperature=1.5、top-p=0.9
   warped behavior token logprob；混合 stop/length 由每行 EOS convention 验证，误差超出
   `5e-4` 直接拒绝 wave。随后最多 2 epoch，按 warped 序列概率 ratio 做 PPO clipping，同时计算完整词表的 behavior KL 和 base
   anchor KL，并以 `1e-3` 权重训练相同的 CE64 value head。超过硬阈值或 canary 失败
   即事务回滚。构造 optimizer 时必须显式包含 policy LoRA 与 value-head 参数。
7. update 全程持有 ledger 跨进程锁。训练前以 PREPARED 状态持久化 before checkpoint；
   接受后持久化 committed checkpoint 与 update receipt（均记录 SHA），再以 transaction ID、
   policy version、wave digest 做 CAS 切换为 COMMITTED。启动发现 PREPARED 就恢复 before，发现
   COMMITTED 就恢复 committed；只有显式安装新 behavior adapter、版本与全局唯一 wave 才能继续。
8. 对多 wave 独立进程运行，发布 checkpoint 额外保存 `training_state.pt`。wave 2 起必须在
   frozen config 的 `pins.resume_training_state` 中逐字复制前一 `DONE.json` 给出的 path/SHA；
   learner 在当前 wave step 前恢复 optimizer、scheduler、全部 RNG、step counter 和 KL beta。

## 7B 的显存实现

逻辑上有 policy、behavior、base anchor 三个分布，但不要复制三份 7B backbone。推荐：

- 单份只读 REAL-Prover 7B backbone；
- 一份可训练 `policy` LoRA；
- wave 开始时复制 LoRA 小权重得到只读 `behavior` adapter；
- 禁用 adapter 得到只读 base anchor；
- 使用 `adapter_reference.py::SingleBackboneAdapterReferences` 暴露 policy facade、behavior
  view 和 disabled-adapter base view；它以 re-entrant lock 覆盖完整 forward/backward/step，
  异常时恢复 policy adapter 与训练模式，并按 manifest 重算 behavior adapter/base identity；
  live base-state getter 为强制接口，backend 自行哈希返回的冻结 tensor mapping，不能用构造时
  缓存的 `ArtifactIdentity` 冒充 live hash；learner 会将 receipt 中的
  behavior/base identity 与 manifest 比对后才允许训练；
- learner 以 micro-batch 累积梯度。每块只长期保留 policy graph；behavior/base 顺序前向，
  立即裁到 action-prefix 行后释放 dense reference 输出。真实 smoke 再冻结 micro-batch 大小。

learner 的快照只复制 `requires_grad=True` 的参数，避免每次更新额外复制约 15GB base
模型；optimizer、scheduler、CPU/CUDA/NumPy/Python RNG 与 KL beta 都会回滚。

## “exact KL”的准确表述

这里 exact 的含义是：在已采样的 action-prefix 上，对完整词表精确求
`KL(policy || reference)`，不是只看已采样 token 的 `Δ²`。状态/prefix 分布仍由有限
rollout Monte Carlo 估计；每个 tactic 内先对 action token 取均值，再按 problem 平衡。
因此报告中必须写“sampled-prefix token-mean full-vocabulary KL”，不能宣称求出了所有 Lean
状态上的全局精确 KL。

## 合并前真实门槛

先用 2 个问题 × 每题 1 次、真实 REAL-Prover、真实 prompt、真实 Lean 4.28.0 运行；
检查 token 对齐误差、截断、显存、adapter 切换、严格回执和回滚。通过后才允许用
`config/online_v2_arm.frozen.json` 的 20-family wave 做小试验；学习率、micro-batch、
precision 等仍须由该 smoke 冻结。

# Online-v2 严格审查整改回执

依据：`../reviews/online_v2_review.md`。状态：B1–B6 已在 CPU 合成边界修复并测试；真实
REAL-Prover/PEFT/Lean smoke 仍未运行，因此尚未放行正式实验。

| Blocker | 修复 | 证据 |
|---|---|---|
| B1 三份整 wave dense logits / 无 micro-batch | `OnlineV2Config.micro_batch_size`；全局 problem 权重切片后累积；behavior/base 每个 micro-batch 重新顺序前向，立刻裁成 action-context logits | `test_microbatch_matches_full_batch_update`、`test_selected_context_kl_matches_dense_reference_implementation` |
| B2 信任 actor mask/advantage/path | 三类冻结 canonical-SHA receipt；builder 重算 mask、baseline、advantage、reward、path distance；learner 只接受 tamper-evident `ValidatedRolloutWave` 并再次验证 | `tests/test_receipt_builder.py`、`test_learner_refuses_loose_actor_samples` |
| B3 wave 可无限重放 | 全局 `wave_id/event_id`；原子持久消费 ledger；accepted 后 seal；显式安装新 behavior 才能继续；`update_epochs ∈ [1,2]` | `test_accepted_update_reports_streamed_kl_and_seals_wave`、`test_new_update_requires_explicit_new_behavior_wave`、`test_update_epoch_cap_is_structural` |
| B4 单骨干 PEFT 只有文档 | `SingleBackboneAdapterReferences`：三视图、`RLock` 覆盖完整 update、异常恢复 policy adapter/training mode、live behavior/base SHA/version 校验；learner 校验并使用 bundle | `tests/test_adapter_reference.py`、`tests/test_adapter_learner_integration.py` |
| B5 value 按 event 等权 | 对有 value label 的子集重新计算 problem-balanced weights，并把 micro-batch value loss 乘回全局质量 | `test_value_gradient_gives_each_problem_equal_total_mass`、`test_verified_path_value_head_uses_problem_balanced_ce64` |
| B6 ±20 clamp 消灭活跃梯度 | 在 log 域按 advantage 符号先选 PPO 分支；float64 exponent；仅用 straight-through 数值边界保证有限值而保留活跃梯度 | `test_extreme_ratio_gradient_in_all_ppo_quadrants` |

B1～B6 阶段的历史验证为 `41 passed`；当时 `scripts/smoke.py` 为 `DONE`，4 samples、
2 independent problems、4 micro-batches、1 optimizer step。当前权威结果见下方 PF 整改段。

## Post-fix PF-1～PF-3

| Blocker | 关闭实现 | reviewer 反例回归 |
|---|---|---|
| PF-1 receipt 内容替换 | verifier 同时绑定 search ID/SHA；proof path 绑定 terminal verifier ID/SHA，并绑定有序 event/search/verifier 内容链摘要 | `test_same_id_search_replacement_cannot_reuse_old_verifier_or_path` |
| PF-2 ledger/checkpoint 分裂与并发重放 | 跨进程 advisory lock 覆盖整个 update；锁内重读与 transaction-id/version/digest CAS；PREPARED 保存 before checkpoint，COMMITTED 保存 post checkpoint + receipt 及两者 SHA；启动恢复明确处理两状态 | `test_commit_then_raise_recovers_committed_model_instead_of_rolling_back`、`test_two_learner_instances_competing_for_one_wave_only_commit_once`、`test_prepared_crash_is_restored_and_wave_can_be_retried` |
| PF-3 PEFT 身份闭环 | search receipt/sample/wave 携带 behavior/base version+SHA；builder 拒绝混合 identity；learner 在任何训练前调用 `validate_wave_identity`；PEFT backend 强制 live base getter | `test_learner_entry_calls_wave_identity_validation`、`test_mutating_frozen_base_fails_before_learning` 及 receipt identity tests |

Post-fix 本机权威验证更新为：`50 passed`；`scripts/smoke.py` 为 `DONE`。真实
REAL-Prover/PEFT/Lean smoke 仍是正式实验前的硬门槛。

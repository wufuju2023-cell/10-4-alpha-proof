# STATUS（本仓库对齐状态 · v0.1）

> 维护约定：每完成一项改状态并在“证据”列挂测试/回执路径。
> 标记：✅ 对齐 ｜ ⚠️ 偏离/待校准 ｜ ❌ 未实现 ｜ ➕ 本机创新 ｜ ➖ 层级替代

## 内核（A1/A2/A3）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| τ=200（可改） | ✅ | `lean/patches/0001`、`config.py`、tests |
| c_AND=64 | ✅ | `lean/patches/0002`、`mcts/search.py`、`test_c_and...` |
| 未访问惩罚=32 | ✅ | 同上、`test_c_and...` |
| -40 兜底 | ✅ | `lean/patches/0003`、`config.py` |
| 渐进采样不重置（G2） | ✅ | `test_g2_reexpansion_never_resets_children` |
| prior 归一（G3） | ✅ | `test_prior_normalization_g3` |
| value target 官方语义（G1） | ✅ | `test_compute_value_target_minimax`、`test_extract_transitions...` |
| Q 变换 / AND 选择 / min 回传 | ✅ | `tests/test_mcts_parity.py` |
| 树规模 / 自适应预算 | ⚠️ | max_nodes/steps=64/64；官方预算模型后置 |

## 网络（A4）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 64 桶价值头（唯一） | ✅ | `tests/test_value_head64.py` |
| two-hot / 期望解码 | ✅ | 同上、`test_value_targets.py` |
| 64 桶 7B 头可加载性 | ✅ | 云端 `head-runs/runs/train205628-full-v3/value-head.pt`（256→64）加载器键名归一后严格匹配；`s18-d64-full205628/head/head.pt` 实为 2048→1 标量头（勿用） |
| 7B bf16 加载 + 前向 | ✅ | 云探针：117s、logits `[1,8,152064]`、hidden 3584 |
| 7B + LoRA 联合更新 | ✅ | 云端 `cloud_real_smoke.py`：真值头严格加载（missing/unexpected=[]）、LoRA applied、离线+在线各 1 次更新；回执 `outputs/cloud_smoke_full/report.json` |

## 更新（A5）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 离线 CE 模式（policy+value） | ✅ | `tests/test_offline_learner.py`、`run_expert_iteration` 回执 |
| 反证样本进训练 / timeout 剔除 | ✅ | 同上、smoke 输出 `replay={'proof':4,'disproof':1,...}` |
| SFT 10% 混比 | ✅ | `OfflineBatchBuilder`、`sft_mix=0.1` |
| 在线 batch=1 / =N | ✅ | `tests/test_online_learner.py`、smoke 输出 4 次 / 1 次 |
| 正奖励须 terminal_verified | ✅ | `test_positive_reward_requires_verification` |
| value 目标不回落 r | ✅ | `test_value_target_optional_but_no_r_fallback` |
| value 权重 1e-3 重标定 | ⚠️ | 配置已切；真实训练校准曲线待跑 |

## 课程机制（curriculum/，本轮新增）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 预算 250×1.17^f / cap 16000 | ✅ | `Scheduler.budget`、`test_budget_growth_and_cap`（默认窗口 25 → 12664；窗口 50 触 cap） |
| trust/mastery 8/12 | ✅ | `test_priority_weights_and_mastery`、`test_strict_mastery_mode...` |
| 反证率 50% + 永久排除 | ✅ | `polarity_for`、`test_polarity_deterministic...`、`test_unknown_blocks...` |
| 优先级四档权重 | ✅ | `priority_weight` 测试 |
| 依赖解锁/推进门槛 | ✅ | `test_dependency_unlocking`、`test_strict_mastery_mode...` |
| 三闸门判定 | ✅ | `test_gates_boundaries`（编译/难度/结构边界） |
| Pell 七课课程表 + 运行器 | ✅ | 演示：14 步跑完 7 课，预算 250→292/课；状态可断点续跑（`test_runner_persistence_and_resume`） |
| teacher 变体生成 / auto-formalization | ❌ | 未接入（闸门只判定） |
| Matchmaker 文件级多进程协议 | ❌ | 未搬迁（单进程轻量版） |

## 环境与测试

| 项 | 状态 | 证据 |
| --- | --- | --- |
| Lean 4.28 环境（Gloway） | ✅ | `/mnt/gloway/projects/lean-4.28-reap`（ext4 bind mount，fstab 持久化）：Lean 4.28.0-rc1 + Reap@0090d73 + v1 补丁 + mathlib cache；`lake build` 7913 jobs 成功，对齐补丁后重编译成功 |
| 纯 CPU 测试（my-new-linux） | ✅ | `8 passed, 3 skipped`（`~/venvs/alpha104`） |
| 含 torch 全量测试（云 ROCm） | ✅ | `19 passed in 4.01s` |
| Pell Mock 端到端冒烟 | ✅ | 搜索 7 节点 / 8 模拟；两种更新回执 |
| 真实 Lean + 单道 Pell 全流程 | ✅ **solved** | 课程第一课 `CourseInvariant` 由对齐内核求解：`out/pell-smoke/sessions-invariant2/pell-invariant-01/result.json` → `solved: true`、`proof_script: "solve | intro x y h; simp only [step]; nlinarith [h]"`（final check + replay check 通过）；归一化 5 条事件。全量 Target 的 mock 流程见 `sessions2/`（exhausted 属预期） |
| 真实 7B 云端冒烟 | ✅ | `scripts/cloud_real_smoke.py`：真值头严格加载、LoRA applied、离线+在线各 1 次更新 |

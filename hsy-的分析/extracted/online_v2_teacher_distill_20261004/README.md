# Online-v2 与学生状态教师蒸馏

## 问题与结论边界

本实验回答两个工程问题：

1. 能否用严格版本绑定、按独立问题组批的 clipped PPO 替代仓库中的 `-rΔ + βΔ²` 在线更新？
2. 能否在官方成功路径 CE 主线上加入 DeepSeek-V4.1-Flash 等外部模型，并保持 Lean 为唯一正确性裁判？

当前状态：**实现完成，8 项单元测试通过，合成 CPU smoke 为 `DONE`；尚未进行真实模型、真实 Lean、真实教师端点或 GPU 效果实验。** 因此交付证明的是目标函数、数据边界和失败回滚可执行，不声称 solve rate 已提高。权威 smoke 证据为 `runs/smoke/receipt.json` 与 `runs/smoke/verification.json`。

便携交付包生成在 `build/online_v2_teacher_distill_20261004_delivery.zip`；解压后顶层目录仍为 `online_v2_teacher_distill_20261004/`，不包含 Python/test 缓存。

主张保持不变：官方成功证明/反证路径 CE 是主线；Online-v2 是实验臂；外部教师最先作为候选生成器接入。

## 已交付

- `src/alphaproof_online_v2/objectives.py`：逐 token clipped PPO、全词表 forward KL、entropy、64 桶剩余步数目标。
- `src/alphaproof_online_v2/buffer.py`：按独立问题计数的单策略版本 rollout wave，以及同状态搜索 Q advantage。
- `src/alphaproof_online_v2/learner.py`：有限 epoch 更新、KL 硬门、梯度裁剪、canary 门和 model/optimizer/RNG 完整回滚。
- `src/alphaproof_online_v2/teacher.py`：OpenAI-compatible/DeepSeek 教师接口、严格 tactic JSON 解析、凭据只读环境变量、全候选回执和 Lean proof-path 过滤。
- `config/protocol.frozen.json`：冻结的比较臂、超参、模型/仓库 revision、奖励和发布门。
- `tests/`：目标函数、版本隔离、问题数语义、教师过滤、更新接受和完整回滚测试。
- `scripts/smoke.py`：无网络、无 Lean、无大模型的确定性接线 smoke；权威输出写入 `runs/smoke/receipt.json`。

## 关键语义

### Online-v2

行为策略在一个 rollout wave 内冻结。每个样本必须带相同 `policy_version` 和逐 token `old_logprobs`。策略目标为：

```text
rho_t = exp(logp_new_t - logp_old_t)
L_ppo = -mean_t min(rho_t A, clip(rho_t, 1-eps, 1+eps) A)
L = L_ppo + beta * KL_full_vocab(policy || frozen_behavior) - c_ent * entropy
```

优势只比较同一 Lean state 下有搜索证据的动作。只有一个动作或所有 Q 相同时优势为零，不把 timeout 的终局失败广播成整条路径的负标签。batch readiness 按独立 `problem_id`，不是 event 数。

### 教师蒸馏

这里的“on-policy distillation”严格称为**学生 on-policy 状态分布上的教师蒸馏**：学生搜索产生状态，外部教师和学生共同提出 tactic，Lean 执行并由完整搜索确认 proof path，只有 `lean_accepted && on_verified_solution_path` 的 tactic 才进入 CE。教师与学生 tokenizer 不同，因此不做伪 logits KL；蒸馏对象是经过验证的 tactic 文本。

## 运行与验证

在本目录执行：

```powershell
python -m pytest tests -q
python scripts/smoke.py
```

当前本机结果：`8 passed`；smoke 接受 1 次由 2 个独立问题组成的更新，更新后精确 KL 为 `5.445171700557694e-05`，并仅接纳位于已验证路径上的 `exact h` 作为蒸馏样本。

真实教师端点使用 `TeacherConfig`，凭据仅从 `DEEPSEEK_API_KEY` 读取，不写入配置、回执或日志。DeepSeek 官方 API 当前模型名固定为 `deepseek-flash`；自部署时必须使用冻结配置中的权重 revision 或另行冻结新 revision。

## 接入现有 AlphaProof 仓库

接线点只有三个：

1. actor 在 tactic 采样时记录逐 token logprob、`policy_version/problem_id/trajectory_id/state_id`；
2. MCTS 对同状态候选输出可比较的 Q，转换为 `SearchObservation`；真实 Lean 搜索把教师候选的结果转换为 `VerificationDecision`；
3. 官方 CE replay 合并 `collect_verified_distillation` 返回的 `DistillationExample`，而 Online-v2 只消费同一冻结版本的 `RolloutSample`。

不要把“Lean 能执行一步”误当成“应进入 CE”；必须已经确认该动作处于完整、严格验证的证明或反证路径。

## 冻结输入与来源

- 对照仓库：`wufuju2023-cell/10-4-alpha-proof@e1652d30e3ad36f0a46eb1bc73960c76bab41a62`
- 教师候选：`deepseek-ai/DeepSeek-V4.1-Flash@2cba9e42aa026125f3ed06c6d98c1db82f7ca027`
- AlphaProof 方法依据：Nature 论文及 Supplementary Data 的成功 proof/disproof replay + policy CE。

## 下一安全动作

先把该包作为依赖接入真实 actor/Lean verifier，运行 2 个问题、每题 4 个尝试的真实模型 smoke；检查显存、逐 token logprob 一致性、终止、proof-path 提取和严格验证。smoke 通过后，冻结题集、种子、搜索/token 预算，依次比较 `official_ce`、`official_ce_plus_verified_teacher_distillation`、`online_v2`、`ce_plus_online_v2`。实质性推理和训练按工作区规则使用可持久化付费 GPU，不直接启动大规模 Kaggle 运行。

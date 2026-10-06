# 10-4-alpha-proof

> AlphaProof 语义对齐版（以《N33 官方设定与本机实现对照总表》为唯一口径）。
> 目标：把搜索（MCTS）与参数更新的**语义/公式/关键超参**对齐官方，
> 规模上明确不复刻（3B 编码解码器 / 300B 预训练 / 80M 课程 / TPU 集群），
> 用 REAL-Prover 7B + LoRA + 64 桶价值头作为有理由的替代。

本仓库当前提供两套**参数更新**实现（用户要求，分目录、各自独立 README）：

| 目录 | 模式 | 监督信号 | batch 语义 | 样本范围 |
| --- | --- | --- | --- | --- |
| [`update_offline/`](update_offline/README.md) | **官方式离线专家迭代** | policy：对“搜索选中动作”交叉熵；value：64 桶 CE（权重 `1e-3`） | `--batch-size`（对应官方 4096，可配） | 证明 + **反证**；timeout 默认剔除；SFT 10% 混比 |
| [`update_online/`](update_online/README.md) | **本机式在线更新（RTTT/TTTRL）** | 一个标量奖励同时驱动 policy（REINFORCE+KL）与 value（64 桶 CE） | `learn_batch_size`：=1 一条轨迹一更；=N 凑 N 条一更（梯度累积） | 已验证轨迹；正奖励须 `terminal_verified` |

两套共享同一个 `alphaproof/` 核心包（搜索、价值目标、价值头、数据结构），
但损失函数、数据流与调度完全分开，互不依赖。

## FATE-M 对照实验

REAL-Prover 7B 的同源搜索路径 CE 与 Online-v2 对照位于
[`experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/`](experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/README.md)。
实际运行20个family的v001、一个共同搜索波，heldout为同族v009/v010共40题。
CE完成20条transition的full-replay更新；Online尝试更新后因冻结KL门禁拒绝并完整回滚，
部署结果按身份审计复用initial评测。原多波协议和双方接受更新门禁均为INCOMPLETE，
40题均为给定目标假设的direct_target脚手架；initial24/40、CE25/40，不能据此宣布数学证明发现能力提升或成功学习后的算法优劣。冻结输入、运行哈希、证据来源和最终数字见实验README。

## 已对齐的 P0 清单（N33）

| 编号 | 项目 | 本仓库实现 | 状态 |
| --- | --- | --- | --- |
| D1 | 先验温度 τ | 默认 **200**（官方 Table 3），运行时可改 | ✅ |
| D6 | c_AND / 未访问惩罚 | **64 / 32**（官方 Table 3），Lean 补丁 + Python 双实现 | ✅ |
| D2 | 失败兜底 | **-40**（官方伪代码），Lean 补丁 + 配置项 | ✅ |
| D5 | 价值头形态 | **仅 64 桶**（无 tanh 标量版），two-hot 标签 + 期望解码 | ✅ |
| G1 | value target | `compute_value_target`：终端 0 / OR `-1+子` / AND `min`；r 不再兜底 | ✅ |
| G2 | 渐进采样 | 重新扩展**只增不减**（不重置 children） | ✅ |
| G3 | 先验归一 | 打分时按 `totalMass` 归一 | ✅ |
| D3 | value 损失权重 | 默认 **1e-3**（可配；64 桶 CE 下需重标定，见 docs/alignment.md） | ✅ |
| D4 | policy 损失 | 双模式：离线 CE（官方）/ 在线 REINFORCE+KL（本机创新） | ✅ |

## 目录结构

```text
alphaproof/                 # 共享核心（无 torch 亦可运行纯算法部分）
  config.py                 # 全部超参：τ=200、c_AND=64、未访问=32、-40、bins=64、value_coef=1e-3
  mcts/                     # 对齐版 PUCT（树 / 搜索）
  env/                      # LeanEnv 三原语协议 + 佩尔题 Mock 环境
  net/                      # ValueHead64（唯一形态）/ 双头前向
  targets/                  # compute_value_target / extract_transitions / two-hot
  data/events.py            # Transition / Trajectory
  pipeline.py               # 搜索→轨迹→训练事件（Mock 版）
  tiny.py                   # 无依赖小模型（冒烟/单测）
update_offline/             # 官方式离线专家迭代（CE）
update_online/              # 本机式在线更新（batch 可配）
curriculum/                 # 课程机制：依赖解锁 + 预算调度 + 三闸门 + 运行器
examples/                   # 数值小例子（可跑的手工数字走查）
lean/patches/               # Reap 内核对齐补丁（τ / c_AND / unvisited / -40）
tests/                      # pytest：价值目标、MCTS parity、两套 learner（torch 自动跳过）
scripts/                    # 冒烟 / 云端脚本 / 密钥检查
assets/pell/                # 佩尔题（来自用户已验收实验的题面）
docs/                       # alignment / status / real_lean_next
```

## 快速开始

```bash
# 纯 CPU：搜索 + 价值目标（无需 torch）
python3 -m pytest tests -q                 # 无 torch 时自动跳过 3 个更新测试
python3 scripts/smoke_one_pell.py          # 单道佩尔题：搜索→轨迹→(有 torch 时)两种更新

# 有 torch（或云端 ROCm）：
python3 -m update_offline.run_expert_iteration --batch-size 8 --steps 1
python3 -m update_online.run_online --batch-size 1    # 一条轨迹更新一次
python3 -m update_online.run_online --batch-size 4    # 凑 4 条更新一次
```

## 课程学习机制（curriculum/）

对齐官方 Table 7 与本机 v1 规格的**轻量调度实现**（只调度、不执行搜索；executor 注入）：

- 预算：`B = min(cap, base × mult^f)`，默认 250 / 1.17 / 16000（f = 窗口内 exhausted 次数）；
- 信任/掌握窗口：8 / 12；优先级权重：interesting 1.0 / undecided 0.1 / fully-proved 0.001 / disproved 0；
- 证明/反证 50% 确定性极性；`disproved` 永久排除；`unknown` 冻结待对账；
- 依赖解锁：默认连续成功 1 次即可推进（`advance_streak` 可配；`strict_mastery=True` 切官方 12 次口径）；
- 三闸门（变体准入判定）：Lean 编译 / 难度 `1−solve@16 ∈ [0.5,0.9]` / 结构 `Sim ≥ 0.7`；
- Pell 七课课程表 `curriculum/pell_course.json`；运行器状态 JSON 原子写、可断点续跑。

```bash
python3 scripts/run_curriculum.py --course curriculum/pell_course.json \
    --state outputs/curriculum_demo_state.json --max-steps 40 --mock fail-once
```

> 未实现（透明声明）：teacher 变体生成 / auto-formalization（官方 Gemini、本机 DeepSeek 三闸门管线）
> 均未接入；闸门只做判定，`solve@16` 与结构相似度需由调用方提供实测值。

## Lean 4.28 环境（Gloway）

- 安装位置：`/mnt/gloway/projects/lean-4.28-reap`（elan 在 `/mnt/gloway/tools/elan`）；
- 版本与 v1 训练容器一致：Lean `v4.28.0-rc1` + Reap `0090d73` + v1 训练补丁 0001–0003 + mathlib `v4.28.0-rc1`；
- 在本仓库 `lean/patches/` 之上再应用 3 个对齐补丁（τ=200 / c_AND / -40），即得到“兼容且对齐”的内核；
- 细节与复现命令见 [`docs/real_lean_next.md`](docs/real_lean_next.md) 与 [`lean/README.md`](lean/README.md)。

## 云 GPU

- 运行副本：`/mnt/workspace/alphaproof-aligned/repo`（容器 `dsw-2230133-...`，ROCm）；
- 模型：`/mnt/workspace/models/REAL-Prover-fe76f68d`；
- 64 桶值头（7B 真值头）：`/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt`（256→64，加载器已做键名归一）。
  ⚠️ 注意：`s18-d64-full205628/head/head.pt` 实测是 **2048→1 标量头**（另一条 2048 特征轨），**不要**当作 7B 64 桶头使用；
- 冒烟：`python3 scripts/cloud_real_smoke.py`（真实 7B + LoRA + 训练头；已实测通过）。

## 安全

- 仓库**不包含**任何 token/密钥/私钥（推送前运行 `scripts/check_secrets.sh` 检查）；
- 不包含官方论文补充材料与第三方源码；Reap 以内核补丁（diff）形式引用，需自行 clone 上游。

## 来源与边界

- 官方口径：AlphaProof 论文补充材料 Table 1–7 与 `pseudocode.py`（不随本仓库分发）；
- 佩尔题：来自用户已验收实验 `20260828-real7b-pell-success`，本仓库只含题面与来源说明；
- 不复刻：3B enc-dec、300B 预训练、80M 课程、TTRL 变体生成、Matchmaker 全套自适应预算（列为后续里程碑）。

## 许可

未指定开源许可证；如需引用请先联系仓库所有者。

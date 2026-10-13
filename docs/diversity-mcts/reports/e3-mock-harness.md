# E3 mock 三臂 harness 报告（纯 CPU 骨架验证）

- 日期：2026-10-08
- 状态：完成（对应 `7-GPU待办清单.md` 的 G3 前置件）
- 脚本：`scripts/e3_mock_three_arms.py` ｜ 结果 JSON：`reports/e3-mock-arms.json`
- **重要**：本报告数字来自确定性 mock 环境，只用于验证"预算口径 / 三臂接线 / 指标落盘"，
  **不是实验证据**；真实 E3（真 Lean + 真 policy/value）待 GPU 恢复后按 G3 执行。

## 1. 目的与口径

把 E3「搜索 vs 采样、同预算对照」的骨架先跑通：

- **预算** = `env.apply` 次数，三臂统一封顶（对应真实实验里的 Lean 验证次数）；
- **三臂**：
  - `sampling`：逐步均匀采样；失败重采、成功前进（best-of-B 的串行版）；
  - `mcts_value`：本机对齐 PUCT MCTS + 完美价值 `V = -剩余步数`；
  - `mcts_novalue`：同一 MCTS，`value=None`（纯先验 + 环境反馈）；
- **环境**：确定性链式 mock。每级 4 个候选：`step`（正常推进）、
  `side`（也推进但总需求 +2，构成"弯路"）、`bad_a` / `bad_b`（直接失败）。先验平（`logprob=0`）。

## 2. 结果（instances=300, depth=4, budget=32）

| arm | solve_rate | 平均 apply（已解） | p50 | p90 | 平均 apply（全部） |
|---|---:|---:|---:|---:|---:|
| sampling | 0.343 | 17.38 | 17 | 28 | 26.98 |
| mcts_value | 1.000 | 16.00 | 16.0 | 16 | 16.00 |
| mcts_novalue | 1.000 | 16.00 | 16.0 | 16 | 16.00 |

观察与解释：

- 采样臂在"每级 25% 命中 + 弯路惩罚"下会明显浪费预算（平均 27 次、解出率仅 34%）；
- 两个 MCTS 臂在本 mock 中完全一致（16 次 = 4 级 × 每级 4 个候选各验证一次）：
  确定性环境 + 平先验下，(b)/(c) 的差异不会显现——**真实 E3 需要分支死角/噪声环境**，
  这正是真实验要测的（判据见 `3-实验计划.md` §E3）。

## 3. 交接（GPU 恢复后）

1. 把 mock env 换成真实 Lean（lmc 会话服务器）+ 真题集（FATE v001 / Pell smoke）；
2. policy/value 换成 `/eval`（G5 的 `lmc/eval_client.py` 已就绪，含重试与 -40 兜底）；
3. 预算口径保持"Lean 验证次数"；指标字段沿用本脚本（solve_rate / applies 分布）。

## 4. 复现

```bash
cd /mnt/gloway/projects/10-4-alpha-proof
PYTHONPATH=. ~/venvs/alpha104/bin/python scripts/e3_mock_three_arms.py \
    --instances 300 --depth 4 --budget 32 \
    --out docs/diversity-mcts/reports/e3-mock-arms.json
```

# new-python-version-mcts-plan · 新版 Python MCTS 规划（spec）

> 状态：v0.1 草稿（2026-10-07）｜目录：`docs/new-python-version-mcts-plan/`
> 代码落地目录（独立仓库，已建）：**https://github.com/wufuju2023-cell/lean-mcts-cpu-server**（本机 `/mnt/gloway/projects/lean-mcts-cpu-server`；
> 本目录是设计规格，实现与「全新机器从零安装」以该仓库为准）
> 语言约定：本目录全部用中文；数学记号遵循 `examples/README.md` 的纯文本惯例（禁止裸 LaTeX）。

## 0. 一句话

把 MCTS 从 Lean 内部（`reapMCTS`）迁到 **CPU 侧 Python 控制器**：Python 管树、调度、数据；
my-new-linux 上的**持久 Lean 会话**管 tactic 执行与状态持有；GPU 侧只做 policy/value 推理与参数更新。
目标是把「搜索」变成可快速迭代的 Python 研究代码，同时把已对齐的官方语义（PUCT / τ / c_AND / 价值目标……）原样继承。

## 1. 背景与动机

- 现状：真实 Lean 搜索入口是 Reap 的 Lean 内 MCTS（`runtime/Smoke.lean::reapTrainingMCTS`，见 `docs/real_lean_next.md`、`lean/pell_smoke/README.md`），已跑通 Pell 第一课（`solved: true`）。
- 问题：搜索逻辑（选择/扩展/预算/调度/数据收集）都在 Lean 里，改一行要编译；与 Python 训练管线（`update_offline` / `update_online` / `curriculum`）靠导出事件对接，迭代成本高。
- 判断：AlphaProof 一类系统的搜索主循环在 Lean 之外（Lean 只做交互式验证环境）；本仓库 `alphaproof/mcts` 已把官方语义对齐好，把它接上「真实 Lean 会话 + GPU 推理」即可复用全部对齐成果。
- Reap 内 MCTS 不废弃：保留为 **baseline / 对照**（同题同预算对比节点数与耗时）。

## 2. 决策记录（2026-10-07）

| # | 决策 | 备注 |
|---|---|---|
| D1 | MCTS 固定在 CPU 侧（my-new-linux） | GPU 侧只做推理与训练 |
| D2 | 先用 Lean **v4.28.0-rc1**（Reap@0090d73 + v1 补丁集） | v4.28.0 正式版对齐后置 |
| D3 | 代码目录 `/mnt/gloway/projects/lean-mcts-cpu-server` | gloway 为 exfat：venv/热状态放 ext4 |
| D4 | 本规划目录 `docs/new-python-version-mcts-plan/` | spec 先行，实现随后 |
| D5 | 网络：默认仅本机（localhost）；远程用 SSH 隧道或 Tailscale | Bearer token 始终保留；禁止公网裸 HTTP |
| D6 | 轨迹口径统一走仓库既有 `Transition` schema | `update_offline` 主线；`update_online` 支线（logprob 由 GPU 侧附加） |

## 3. 设计要点（摘要）

1. **协议先行**：Python↔Lean 用「持久会话 + 状态句柄」协议（03）；Python↔GPU 用 HTTP `/eval` 契约（02 §5）。
2. **语义不变**：超参一律沿用 `SearchConfig` 官方口径；树代码遵循 examples/N-02 约定（显式栈、parent 指针、`path_to_root`）。
3. **并发**：K 个搜索任务 × N 个 Lean 会话（单会话 FIFO）；评估走 GPU 服务端连续批处理（v0 不做客户端批处理）。
4. **数据**：每个搜索一个目录；solved 时 `extract_transitions` 产出 `Transition`；独立严格编译留 `strict_receipt.json`。
5. **验收**：Pell 第一课在「新链路」下 solved + 严格复验 PASS；导出 shard 能被 `update_offline` 冒烟消费。

## 4. 文件索引

| 文件 | 内容 |
|---|---|
| `01-requirements.md` | 目标 / 非目标 / 硬约束 / 成功标准 |
| `02-architecture.md` | 组件、进程、数据流、部署、GPU 契约、版本管理 |
| `03-lean-session-protocol.md` | 持久 Lean 会话协议（本规划核心） |
| `04-python-mcts-controller.md` | 控制器与算法：迁移清单、并发模型、节点约定 |
| `05-trajectory-store-and-export.md` | 轨迹仓库与训练导出（含 strict receipt） |
| `06-cli-and-ops.md` | CLI、配置、systemd、metrics、安全、故障处理 |
| `07-bench-roadmap-risks.md` | 基准方法、里程碑、开放问题、风险 |
| `08-testing-and-load-bench.md` | 正确性测试分层、负载标定（N 扫描）、与原管线（96.7% Lean）的三层对照 |

## 5. 与现有资产的关系

**复用（不改语义，只换后端）**

- `alphaproof/mcts/{tree,search}.py`：PUCT、c_AND、渐进采样、AND/OR、去重规则；
- `alphaproof/env/base.py`：`LeanEnv` 三原语（`pp_state` / `apply` / `check_proof`）——新 `RemoteLeanEnv` 实现同一协议；
- `alphaproof/targets/value_targets.py`：`compute_value_target` / `extract_transitions` / `distance_to_two_hot`；
- `alphaproof/data/events.py`：`Transition` / `Trajectory` schema；
- `alphaproof/config.py`：官方对齐超参；
- `update_offline/`、`update_online/`、`curriculum/`：训练与调度侧原样接入。

**参考（v1 容器资产）**

- `/mnt/gloway/projects/reap-new-update-model/v1-result/20260828-real7b-pell-success/code/`
  - `cpu_runtime/`：`batch_solver.py`（`lake env lean <theorem_file>` 编排）、`mock_services.py`、`normalize_rollout.py`；
  - `containers/cpu/`：`reap-overlay/`（含 `Reap/Training/RolloutSink.lean`）、`verified-collector-overlay/`、`selection-value-refresh-overlay/`、`mathlib-replay/`、`verified-replay/`、`patches/`、`smoke_cpu.sh`。

**对照**

- Reap Lean 内 MCTS：`/home/a/lean-4.28-reap/runtime/Smoke.lean`（`reapTrainingMCTS`）与 `reap/Reap/Tactic/TreeSearch.lean`。

**阅读约定（examples/）**

- `examples/N-02_class-node-exercise.ipynb`：节点实现约定（显式栈两阶段后序、parent 指针、`path_to_root`、两层 AND 手算表）——新版树代码按此风格；
- `examples/update_modes_walkthrough.ipynb|py`：价值目标与两种更新的数值走查——导出/校验时作为对照；
- `examples/00-风格与编号规范.md`：公式与编号风格（纯文本记号）。

## 6. 术语

| 术语 | 含义 |
|---|---|
| session（会话） | 一个常驻 Lean 进程 + 其持有的状态集合 |
| state / state handle | 会话内的证明状态句柄（如 `s/17`），Python 只持句柄 |
| search / search_id | 一次 MCTS 搜索任务（对应一道题的一次尝试树） |
| wave | 一个训练波次（同一 policy_version 下的若干搜索） |
| shard | 导出的训练分片（JSONL） |
| strict receipt | 独立整文件编译的严格复验收据 |
| policy_version | 生成轨迹时的策略版本标签（GPU 侧注入） |

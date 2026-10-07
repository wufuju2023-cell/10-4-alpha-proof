# 08 · 正确性测试、负载标定与对照基准

> 目标：① 验证 lean-backend + python-mcts 服务器的正确性；② 标定 my-new-linux 的**最大负载**（推荐并发 N）；
> ③ 与「原管线」（96.7% 时间在 Lean，单次尝试 ~25.9s）做同口径对照。

## 0. 对照基准：原管线的事实（hsy-的分析 / ModelScope 交付）

| 指标 | 原管线实测 |
|---|---|
| 初始评测 | 40 题 / 106 次尝试 / wall 2839.95s（47.33 min） |
| 生成（GPU） | 70.20s ≈ **2.5%** |
| Lean 验证 | 2747.41s ≈ **96.7%** |
| 单次尝试 | 平均 Lean 验证 ≈ **25.9s**（整文件独立编译，**每次尝试都重编译**） |
| CE 评测 | 103 次尝试 / wall 2761.34s；生成 69.28s；Lean 2670.39s |
| 训练 | CE 20 条 transition / 137 token / 16.8s；Online 233 候选 2 epoch / 746.4s（被拒） |

关键结论：原管线的「贵」不在生成（每次尝试平均只生成 ~6.9 token），而在**每次尝试整文件重编译**（Mathlib import 等固定开销）。
原评测为**串行**：106 次×25.9s → Lean 侧有效吞吐 ≈ **0.039 次/秒**——这就是要打的靶子。
新后端的对照核心 = 把固定开销从「每次尝试」降为「每会话一次 + 每步增量」。

> 口径公平性：25.9s 来自云端实例，硬件与本机不同。**旧/新对照必须在同一台机器（my-new-linux）上重测**；
> hsy 数字作为背景参考；旧跑法用本机可复现的 v1 `cpu_runtime.batch_solver`（每次尝试 `lake env lean` 新进程）复刻。

## 1. 测试分层（正确性）

### L1 · 协议单测（mock Lean，socket 级）
- mock session 实现 03 协议；测点：`open/apply/branch/goals/release/close/ping`、错误码、超时、半行/乱序恢复、消息截断、FIFO；
- 判据：全绿 + 随机消息序列 fuzz 不崩。

### L2 · MCTS 语义回归（无 Lean）
- 仓库既有 `tests/test_mcts_parity.py` 全绿；
- 新增：迭代版 vs 递归版逐节点一致（N-02 两层 AND 表 + 随机树 1000 棵对拍）；
- mock env + mock eval 端到端搜索冒烟。

### L3 · 单会话真 Lean（功能核心）
- Pell 第一课 `PellInvariantSmoke.lean` → solved；best_script 与 `sessions-invariant2` 的
  `solve | intro x y h; simp only [step]; nlinarith [h]` 一致；
- 严格复验（fresh 进程整文件编译）PASS；负例（注入 `sorry`）必须 FAIL；
- **分叉正确性**：同一状态 8 个候选 tactic 分别 apply，子目标数与内容正确；失败的 apply 不影响其他状态；
- **对拍**：路线 A（会话）vs 路线 C（重放）在 100 组 `(state, tactic)` 上结果一致。

### L4 · 池与故障（工程）
- 搜索中 `kill -9` 一个 worker：搜索失败落盘、池 ≤5s 恢复、无僵尸；
- apply 超时（注入慢 tactic）：杀 worker、会话作废、任务降级；
- 版本不匹配：`open` 拒绝；内存护栏：拒绝新搜索、正常回收。

### L5 · 数据闭环（训练）
- Pell 一波 → `lmc export` → `run_expert_iteration` 离线 1 步成功；
- merge logprobs 后 → `update_online` learner 1 步成功；
- `strict_receipt.json` 字段完整（哈希 + 版本三元组）。

## 2. 负载标定（给定硬件的最大吞吐）

### 2.1 两个基准负载

**W1 · apply 风暴（测 Lean 后端上限，无 GPU 依赖）**
- 语料：固定题集上预生成 10k 个 `(state, tactic)` 对（成功:失败 ≈ 8:2；覆盖 1/2/3 子目标与长短战术）；
- 分发：open-loop、每会话 1 个 in-flight、round-robin；
- 指标：applies/s、p50/p95/p99、错误率、CPU 利用率（`mpstat -P ALL 1`）、RSS/会话、上下文切换。

**W2 · 端到端搜索（测全链路）**
- 题集：FATE-M 20×v001（本机已解压）或课程前 7 课；固定预算（如 max_nodes=256）；
- 策略：先 mock eval（确定性，消除 GPU 变量）；再真 GPU 一轮做对照；
- 指标：题/小时、nodes/s、Lean/生成/调度占比、solved 率、队列深度。

### 2.2 并发扫描（N = 2 → 12）
对每个 N：预热 → 稳定运行 10min → 记录曲线。结论：
- `N*`：吞吐饱和点（再增 N 吞吐不涨）；
- `N_ram`：内存上限（N × RSS + 余量 ≥ 44G）；
- `N_rec = min(N*, N_ram) - 1`（留 1 会话余量 + 2–4 核给系统）。
每轮确认：worker 单核吃满、无僵尸、RSS 无漂移。

### 2.3 稳定性、退化与过载
- soak：N_rec 连续 2h，RSS 漂移 <5%、p95 漂移 <20%；
- 降级：断开 GPU（mock 关闭）→ -40 兜底路径吞吐；
- 过载：队列超阈值 → 背压生效（拒绝而非雪崩）。

### 2.4 产出
`bench-report.json`：

```json
{"hardware": {"cpu": "Ryzen 7 5700X", "threads": 16, "ram_gb": 60},
 "workloads": {"W1": {}, "W2": {}},
 "sweep": [{"n": 4, "applies_per_s": 0, "rss_gb": 0, "p50_ms": 0, "p95_ms": 0}],
 "recommended_workers": 0, "expected_throughput_apply_per_s": 0,
 "bottleneck": "lean|cpu|ram|gpu|queue", "notes": ""}
```

## 3. 与原管线的对照（三层，同口径）

### C1 · 单步对照（隔离 Lean 固定开销）
同一道题、同一段脚本，两种跑法：
- 旧：每次尝试 `lake env lean <file>` 整文件编译（含 import）→ 复现 ~25.9s 量级；
- 新：持久会话逐 tactic `apply`（增量）。
指标：`speedup = t_old / t_new_total`；分解报告「固定开销 / 增量开销」。

### C2 · 单题端到端（评测语义）
同一题集（如 5 题 × 最多 4 次尝试、脚本策略固定）：
- 旧：尝试 = 整文件编译（每尝试一次编译）；
- 新：会话增量 + **每成功题仅 1 次**严格复验；
指标：wall、Lean 占比、每解决题成本。**严格复验这道闸不得为跑分省掉。**

### C3 · 波次吞吐（真实工作负载）
20 题 × 固定预算，新旧链路各一轮（mock 策略一档 + 真 GPU 一档）：
指标：题/小时、Lean 占比（对照 96.7%）、生成占比（对照 2.5%）、solved 率。
预期（待实测）：Lean 占比与生成降到同量级或以下；瓶颈转到 GPU eval 吞吐或会话调度。

### 对照口径声明（写进报告）
- 只做「同题、同脚本、同预算」对照，不做跨题集/跨硬件泛化结论；
- 新旧都保留完整严格复验；差异只允许来自「编译方式与调度方式」。

## 4. 执行顺序（与 07 里程碑对齐）

| 步骤 | 内容 | 产出 |
|---|---|---|
| M0 | W1 微基准 + C1 单步对照 | 速度倍数量级 + N 初值 + `bench-report.json` 首版 |
| M1 | L1–L3 正确性 + C2 单题端到端 | 功能验收（Pell solved + 严格复验 PASS） |
| M2 | W2 + 并发扫描 N=2→12 + soak | `N_rec` 与吞吐曲线 |
| M3+ | 真 GPU 档 C3 对照 + 过载/降级 | 全链路性能画像 |

## 5. 硬件备注

- my-new-linux：Ryzen 7 5700X（8C/16T）、60G（现可用 ~44G）、当前 load ~2.6（与他人共用）→ worker 绑核 + `nice`；
- gloway exfat：基准日志/state 放 ext4（`~/lmc-store`）；
- 结论必须带条件：`recommended_workers` 仅对「当前硬件 + rc1 + 当前外部负载」成立。

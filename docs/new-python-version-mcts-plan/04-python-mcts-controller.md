# 04 · Python MCTS 控制器

## 1. 迁移清单（相对 `alphaproof/mcts`）

**语义零漂移（不得改）**

- PUCT：`u = c(n) · p / (n_a+1)`，`c(n) = c_init + log((n+c_base+1)/c_base)`，先验打分时按 totalMass 归一（G3）；
- `Q = γ^(-1-V)`；AND 节点探索项 × c_AND=64；未访问子 `Q = V(s) - 32`；
- τ=200（先验 `exp(logp/τ)`）；渐进采样 `n_eval ≤ ps_c · n^ps_alpha`（仅 OR）；
- 同战术先验累加、同状态 key 合并；focus 伪动作边代价 0；无合法动作 / 网络失败兜底 -40；
- `value_target`：终端 0；OR `-1+子`；AND `min(子)`。

**替换 / 新增**

| # | 项 | 说明 |
|---|---|---|
| 1 | `PellMockEnv` → `RemoteLeanEnv` | 实现 `LeanEnv` 协议（`pp_state` / `apply` / `check_proof`），底层走 03 协议 |
| 2 | policy/value `Callable` → `EvalClient`（async HTTP） | `/eval` 返回 K=6 候选+logprob+价值；重试与 -40 兜底 |
| 3 | 同步 `search()` → asyncio `SearchTask` | K 个并发搜索任务；每任务一棵树 |
| 4 | 递归遍历 → 显式栈（N-02 约定） | `compute_value_target` / `extract_transitions` / `_mark_optimal` / `_collect_script` 迭代化 |
| 5 | 节点加 parent 指针 | `PNode` 模式：`add()` 内维护 `parent` / `parent_action`；`path_to_root()` |
| 6 | 树序列化 | `tree.json` + `steps.jsonl`（见 05） |
| 7 | 预算来源 | 从 `curriculum.Scheduler.budget()` 取（v0 可用 CLI 固定） |

## 2. 并发模型

```text
asyncio 事件循环
 ├─ SearchTask × K（默认 K = N_sessions，可更高）
 │   每步：await env.apply(...)          # 路由到某个已 open 的会话（绑定后不变）
 │         await eval_client.eval(...)   # HTTP 出站
 ├─ SessionPool: N × lean-session-server（FIFO 队列，忙闲看板）
 ├─ Store writer（批量 append + 关键点 fsync）
 └─ Watchdog（心跳 / 超时 / 重启）
```

- **搜索任务与会话绑定**：一个搜索固定用一个会话（状态句柄会话内有效）；会话失效 → 搜索失败落盘。
- **排队**：会话被占用时请求入队；池满（全部忙）则新搜索等待（背压）。
- **内存护栏**：可用内存 < 6GB 时拒绝新搜索。
- **EvalBatcher（可选）**：跨任务在 5–20ms 窗口内合并 `eval` 请求（批 ≤64）；v0 关闭，若 GPU 侧无连续批处理再开。

## 3. 搜索任务生命周期

```text
schedule(problem, budget, policy_version)
 → open 会话（根状态）
 → loop { select → expand → backprop }   # 直到 solved / 预算尽 / 错误
 → finalize:
     solved → compute_value_target + extract_transitions（仅 is_optimal 子树）
     check_proof（严格复验，见 05 §4）
 → report（result.json / tree.json / steps.jsonl）
 → close / recycle 会话
```

## 4. 与课程调度器的接入

- 从 `curriculum.Scheduler` 取 `budget(lesson_id)`、`priority_weight`、`polarity_for`（反证题）；
- `record(AttemptResult)` 回写 solved / exhausted（驱动信任窗口与预算增长）；
- 反证题：搜索目标为「否定」的闭合（与证明同链路，语义为 disproof）；
- v0：CLI 传显式 manifest（与 pell_smoke 一致）；v1：接 Scheduler + 断点续跑状态文件。

## 5. 节点与树实现约定（遵循 examples/N-02）

- **遍历一律显式栈**：两阶段后序（先 `(node, False)` 压栈、回头 `(node, True)` 结算）；AND 子节点反序压栈保持与递归一致的访问顺序；
- **parent 指针**：采用 `PNode` 子类，`add()` 内双向连接；对 DAG（一个状态多父）记录 `parents: list` + `primary_parent`；
- **`path_to_root(node)`**：供轨迹提取、调试与 best_script 重建（N-02 §2 断言示例照搬为单测）；
- **值目标计算**：

  ```text
  OR:  v(n) = -1 + v(最优子)
  AND: v(n) = min(v(所有子))
  terminal: 0
  ```

- 新增单测：迭代版与递归版逐节点一致（照 N-02 的两层 AND 手算表 + 随机树对拍）。

## 6. 事件与日志（steps.jsonl）

| 事件 | 字段 |
|---|---|
| `open` | search_id, problem_id, session, root, policy_version |
| `select` | path (state ids), actions |
| `expand` | state, tactic, ok, num_goals, new_state(s), prior, logprob |
| `eval` | prompt_hash, k, value, model_revision, latency_ms, cache_hit |
| `backprop` | path, leaf_value |
| `solved` / `exhausted` | best_script, nodes, steps, elapsed |
| `final_check` | ok, receipt 路径 |
| `error` | class, message |

- 所有事件含 `ts`（ISO8601）与 `seq`（单调递增序号，用于残缺检测）。

## 7. 测试策略

| 层 | 内容 |
|---|---|
| 单元 | 树 / PUCT / backprop / value_target（沿用 + 迭代版对照）；`RemoteLeanEnv` 的 mock 实现 |
| 集成 | mock Lean（PellMockEnv）+ mock GPU；真 Lean 单题（Pell 第一课）；小波次（3–5 题） |
| 回归 | `tests/test_mcts_parity.py` 全绿；严格复验样例通过 |
| 对照 | 与 Lean 内 `reapMCTS` 同题同预算对比（节点数 / 耗时 / 解路径，仅观察不设门槛） |

# 03 · 持久 Lean 会话协议（spec v0.1）

> 目标：让 Python 控制器以「状态句柄」方式在真实 Lean 上执行 tactic、分支搜索、取回目标文本；
> 会话实现与协议解耦（路线 A/B/C 见 §6，spike 决定）。

## 1. 传输与生命周期

- **传输**：stdio，每行一个 JSON 对象（UTF-8、`\n` 结尾）。后续如需可换 Unix socket，消息内容不变。
- **一个会话 = 一个 Lean 进程**：由 Python 侧 `SessionPool` 拉起与监管。
- 启动参数（示例）：

  ```text
  lean-session-server --project-dir <runtime> --idle-ttl 900 --max-states 200000
  ```

  环境：`ELAN_HOME`、`PATH` 由控制器注入。
- 心跳：控制器每 10s 发 `ping`，连续 3 次无响应 → `SIGTERM` → 5s → `SIGKILL`，会话作废并重建。
- 会话不复用为多题：一题一搜索一进程（简单可靠）；进程池负责复用成本（预热）。

## 2. 消息总表

| 方向 | 消息 | 说明 |
|---|---|---|
| C→S | `open` | 打开定理，得到根状态 |
| C→S | `apply` | 在指定状态上执行一条 tactic（允许任意历史状态 → 分支） |
| C→S | `goals` | 取目标文本（惰性；pp 缓存） |
| C→S | `release` | 释放状态（GC 提示，可缺省） |
| C→S | `close` | 关闭会话（幂等） |
| C→S | `ping` / `info` | 健康检查 / 版本信息 |
| S→C | `progress` | 进度事件（节点 / 步数 / 耗时；推送，可关闭） |
| S→C | `log` | 诊断（stderr 兜底） |

## 3. 消息细节

### 3.1 open

```json
→ {"op":"open","theorem_file":".../PellInvariantSmoke.lean","theorem_name":"CourseInvariant"}
← {"ok":true,"session":"s1","root":"s/1","num_goals":1,
   "goals_pp":"⊢ CourseInvariant","info":{"lean":"4.28.0-rc1","mathlib":"v4.28.0-rc1","reap":"0090d73c"}}
```

- `theorem_file` 或 `theorem_text` 二选一；`theorem_name` 可省（文件只有一题时）。

### 3.2 apply（协议核心）

```json
→ {"op":"apply","state":"s/1","tactic":"intro x y h","id":"r7"}
← {"id":"r7","status":"ok","num_goals":3,"new_state":"s/2",
   "subgoals":["s/2/g0","s/2/g1","s/2/g2"],"closed":false,
   "state_hash":"9f2c...e1","elapsed_ms":42}
```

- 语义：在 `state` 的**全部目标**上执行 tactic（与官方一次扩展一致）；`num_goals==0` → `status:"closed"`，无 `new_state`。
- **分支**：同一 `state` 可反复 `apply` 不同 tactic；所有状态在会话内保留，直到 `release` / `close`。
- **隔离**：一次 apply 失败 / 超时**不得污染**其他状态；实现要对执行做快照或等价保护（§6 路线要点）。
- `state_hash`：目标文本（pp）的 sha256 前 16 位，控制器用于去重与日志（不是 Key 本身）。
- `status:"error"` 时附 `error_kind ∈ {"parse","elab","tactic_failed","internal"}` + `message`（截断 ≤2KB）。

### 3.3 goals / release / close / info

```json
→ {"op":"goals","state":"s/2"}          ← {"ok":true,"goals_pp":"x y : ℕ\nh : ...\n⊢ ...","cached":true}
→ {"op":"release","states":["s/2/g1"]}  ← {"ok":true,"released":1}
→ {"op":"close"}                        ← {"ok":true}
→ {"op":"info"}                         ← {"ok":true,"lean":"...","mathlib":"...","reap":"...","patchset":"...","project":"..."}
```

### 3.4 progress（S→C，可关）

```json
{"event":"progress","search_step":12,"nodes":37,"solved":false,"elapsed_ms":1840}
```

## 4. 错误与超时

| 情况 | 处理 |
|---|---|
| apply 超时（默认 30s，可配） | 杀 worker；会话作废；调用方节点记 `timeout` |
| 进程崩溃 | `SessionPool` 重启新会话；当前搜索失败并落盘 |
| 版本不匹配 | `open` 校验版本三元组；不一致直接拒绝（防混用 rc1/final） |
| Lean 内部错误 | `error_kind=internal` + 原始消息（截断）；不算 tactic 失败 |

## 5. 顺序与并发

- 单会话内消息严格 FIFO 串行；跨会话并行。
- 控制器保证：同一 `state` 不并发 apply（无意义且可能触发实现告警）。

## 6. 实现路线（spike S-1 决定）

| 路线 | 要点 | 优点 | 风险 |
|---|---|---|---|
| A. Reap 原生驱动 | 在 `ReapRuntime` 增加 `lean-session-server` 主循环；复用 `ProofCheckContext` / `GeneratedTactic` / `TreeSearch` 内部原语；在 mvar 集合上直接执行，免重放 | 零重放、分支便宜、最快 | 需要读改 Reap 内部；快照 / 隔离机制要验证 |
| B. Lean LSP | `lean --server` + 每状态文件 / 快照（LeanDojo 式） | 稳定、与 Reap 版本解耦 | 每步文件同步；goals 走 RPC；分支文件管理复杂 |
| C. 重放兜底 | 每 apply 从根重放至该状态（前缀缓存） | 实现简单、正确性裁判 | 慢；仅兜底与对拍 |

**Spike 判据**：warm apply p50 ≤ 500ms（目标 ≤100ms）；同位分叉 8 候选不串扰；失败不污染；与路线 C 对拍 100 例一致。
**建议**：A 为主修方向，C 作为对拍。若 A 在 2 天内不可行，切 B。

## 7. 与 v1 资产的关系

- v1 `batch_solver` 以 `lake env lean <theorem_file>` **每会话一次进程**；本协议是「进程常驻 + 状态句柄」的升级版。
- v1 `observer` / `RolloutSink` 事件 → 由本协议 `progress` 流 + Python 事件流替代（关键字段对齐）。

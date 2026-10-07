# 02 · 架构

## 1. 组件图

```text
                      my-new-linux (CPU 侧)
┌─────────────────────────────────────────────────────────────┐
│ lmc controller (Python, asyncio)                            │
│   ├─ SearchTask × K        一棵树/任务（复用 alphaproof.mcts）│
│   ├─ SessionPool           N 个 Lean 子进程（FIFO 串行）      │
│   │     └─ lean-session-server × N（stdio JSON）             │
│   ├─ EvalClient            HTTP → GPU /eval（policy+value）   │
│   ├─ Store                 waves/<...>/ steps/tree/result    │
│   └─ Admin (CLI + 可选 HTTP, 默认 127.0.0.1)                 │
└─────────────────────────────────────────────────────────────┘
        │(出站 HTTP)                          ▲(出站 rsync)
        ▼                                     │
  GPU 侧: policy/value 推理服务 + 训练(update_offline/online)
```

## 2. 进程模型

| 进程 | 数量 | 职责 | 备注 |
|---|---|---|---|
| `lmc serve` | 1 | 控制器 + 可选管理 API | asyncio；可 systemd 常驻 |
| `lean-session-server` | N | 持久 Lean 会话（协议见 03） | 子进程；每个加载一次 Mathlib |
| strict 工位 | 按需 | 独立整文件编译（严格复验） | 一次性进程，低并发（1–2） |

## 3. 数据流（一次搜索）

```text
调度取题 → open 会话根状态
  loop:
    select (PUCT)                # 纯 Python
    expand:
      env.apply(state, tactic)   # 本机会话短往返（03 协议）
      policy/value → GPU /eval   # 出站 HTTP，K=6 候选+logprob+价值
    backprop                     # 纯 Python
  直到 solved 或 预算耗尽
finalize:
  value_target 回溯（targets/value_targets.py）
  extract_transitions → transitions.jsonl
  check_proof（严格）→ strict_receipt.json
report → result.json / tree.json
```

## 4. 部署与目录

```text
代码    /mnt/gloway/projects/lean-mcts-cpu-server          # exfat，仅源码/脚本
venv    ~/venvs/lean-mcts                                  # ext4（必须）
热状态  ~/lmc-store/                                       # ext4，原子写
归档    /mnt/gloway/projects/lean-mcts-cpu-server/archive/ # 定期 rsync
配置    ~/.config/lmc/config.toml
```

- gloway exfat 不支持符号链接 / 可执行位：**不要**在 gloway 上建 venv；如需路径语义，参考 `lean-4.28-reap` 的 ext4 bind mount 方案。
- store 采用「临时文件 + rename」原子写；重要事件即时 fsync。

## 5. GPU 侧契约（policy/value）

统一一个端点（共享 backbone 一次前向两路输出，官方口径）：

```text
POST /eval
  { "prompt": "<tactic state 文本>",
    "k": 6, "temperature": 1.0,
    "policy_version": "v123", "request_id": "..." }
→ 200 { "candidates": [ {"tactic": "...", "logprob": -3.21}, ...×k ],
        "value": { "v": -7.4, "d_hat": 7.4 },       # v = -d̂（64 桶期望）
        "model_revision": "REAL-Prover-fe76f68d",
        "adapter": "..." }

错误语义：
- 超时 / 5xx → 客户端重试 3 次（指数退避）→ 仍失败：该节点按官方 -40 兜底（SearchConfig.no_legal_actions_value）
- 兜底事件必须落盘（kind=env_error），供审计
```

- 批处理：v0 依赖服务端连续批处理；控制器留 `EvalBatcher` 接口（窗口 5–20ms、批 ≤64）备用。
- token：`Authorization: Bearer <token>`，token 文件 600 权限；不进日志。
- 兼容说明：v1 smoke 的 policy/value URL（`/sessions/<id>/policy|value`）视为旧格式；新实现以 `/eval` 为准，由 GPU 侧适配层决定。

## 6. 版本与身份

- 每个 job / wave / shard 记录：
  `lean_version=4.28.0-rc1`、`mathlib_rev=v4.28.0-rc1`、`reap_commit=0090d73c`、`patchset_hash=...`、`policy_version`、`config_hash`；
- 收据与 shard 的 sha256 入库（`manifest.json`）。

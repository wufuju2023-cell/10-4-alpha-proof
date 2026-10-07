# 06 · 服务、CLI 与运维

## 1. CLI（`lmc`）

| 命令 | 说明 |
|---|---|
| `lmc serve [--config ...]` | 启动控制器（含会话池）；systemd 托管 |
| `lmc status` | 池 / 队列 / 内存 / 负载 / 近 N 分钟统计（人读表格） |
| `lmc search --problem <f> --budget N --policy-version vN [--out ...]` | 单次搜索（调试 / 小批次） |
| `lmc wave --manifest M --policy-version vN` | 一波次批量搜索（顺序 / 并发按池自动） |
| `lmc bench [--workers 4,6,8] [--n 200]` | 基准：延迟 / RSS / 吞吐 → `bench-report.json` |
| `lmc export --wave W [--shard-size 4096] [--merge-logprobs F]` | 导出训练 shard + manifest |
| `lmc verify --strict --search <id>` | 严格复验并写 receipt |
| `lmc gc [--older-than 30d] [--keep-result]` | 清理大对象 |
| `lmc versions` | 工具链 / 补丁 / 提交清单打印 |
| `lmc session [list\|inspect\|msg]` | 会话调试（发原协议消息） |

## 2. 配置（`~/.config/lmc/config.toml`）

```toml
[paths]
store = "/home/a/lmc-store"
archive = "/mnt/gloway/projects/lean-mcts-cpu-server/archive"
project_dir = "/mnt/gloway/projects/lean-4.28-reap/runtime"

[lean]
elan_home = "/mnt/gloway/projects/lean-4.28-reap/elan"
workers = 6
apply_timeout_ms = 30000
idle_ttl_s = 900
warmup = 4                      # 开机预热会话数

[gpu]
eval_url = "http://<gpu-host>:18080/eval"
token_file = "/home/a/.config/lmc/token"
eval_timeout_ms = 5000
retries = 3
fallback_value = -40.0

[search]
max_nodes = 1000
max_steps = 1000
k_samples = 6

[admin]
bind = "127.0.0.1:8790"         # 默认仅本机
token_file = "/home/a/.config/lmc/admin_token"

[guard]
min_free_mem_gb = 6
```

## 3. 进程与 systemd

- `lmc.service`（user unit）：`ExecStart=%h/venvs/lean-mcts/bin/lmc serve`；`Restart=on-failure`；`MemoryHigh/Max` 视 60G 整机设（建议 Max=45G）；
- `lmc-warmup.service`（oneshot）：开机预热 N 会话；
- 绑核：worker 进程独立分配 CPU（`taskset` / cgroup），保留 2–4 核给系统；
- 日志：JSONL → journald + `~/lmc-logs/`（rotation 7d）。

## 4. 可观测性（/metrics 文本，Prometheus 兼容）

| 指标 | 说明 |
|---|---|
| `lmc_sessions_total{state}` | idle / busy / crashed |
| `lmc_apply_latency_ms`（p50/p95） | 会话 tactic 执行 |
| `lmc_eval_latency_ms`（p50/p95） | GPU 推理往返 |
| `lmc_queue_depth` | 等待会话的请求数 |
| `lmc_solved_total` / `lmc_errors_total{class}` | 结果与错误分类 |
| `lmc_rss_bytes`（controller / worker） | 内存占用 |

## 5. 安全

- 默认 `127.0.0.1`；远程访问：SSH 隧道（推荐）或 Tailscale；token 始终开启（Bearer）；
- 凭据只在环境变量 / 600 文件；日志过滤 `token / password / Authorization`；
- GPU 侧 token 仅控制器持有；不写入任何落盘数据。

## 6. 故障处理

| 故障 | 处理 |
|---|---|
| 会话挂死 | watchdog：SIGTERM → 5s → SIGKILL；≤5s 恢复；搜索失败落盘 |
| GPU 不可达 | 重试 3 次 → 节点 -40 兜底 + `error` 事件（不静默） |
| 存储写失败 | 拒绝新任务（背压），保留现场；恢复后 `lmc gc` 对账 |
| 版本不匹配 | `open` 拒绝（防混用 rc1 / final）；提示 `lmc versions` |
| 内存水位 | 拒绝新搜索；现有搜索完成后回收 |

# 01 · 需求与约束

## 1. 目标（Goals）

| # | 目标 | 说明 |
|---|---|---|
| G1 | 真 Lean 上的 Python MCTS | 用 `RemoteLeanEnv` 把 `alphaproof/mcts` 接到持久 Lean 会话；语义与官方对齐口径零漂移 |
| G2 | 单机吃满硬件 | my-new-linux（16T / 60G，现可用 ~44G）：N 个 Lean 会话并发 + 异步控制器，吞吐随 N 近似线性直到内存上限 |
| G3 | 轨迹即训练数据 | 每个搜索落盘（树/事件/值目标）；solved 轨迹一键导出 `update_offline` / `update_online` 可消费 shard |
| G4 | 可运维 | `lmc` CLI + systemd + metrics + 看门狗；失败可定位、可复现 |
| G5 | 与 GPU 解耦 | policy/value 走 HTTP 契约 + 版本字段；本机默认零入站暴露 |

## 2. 非目标（v1 明确不做）

- auto-formalization / teacher 变体生成（只留挂点，见 04 §4）；
- 多机 Lean 池（协议预留 `worker_addr`，v1 单机）；
- v4.28.0 正式版切换（后置；接口留 `toolchain` 参数）；
- 训练侧改动（`update_offline` / `update_online` 原样使用）；
- 客户端评估批处理优化（v0 依赖 GPU 服务端连续批处理；留 `EvalBatcher` 接口）。

## 3. 硬约束

| 类别 | 约束 |
|---|---|
| 工具链 | Lean `v4.28.0-rc1`；Mathlib `v4.28.0-rc1`；Reap `0090d73c` + v1 补丁集（含 Training overlay）；全部哈希进收据 |
| 主机 | my-new-linux；gloway = exfat（代码可放；**venv 与热状态必须放 ext4**，参考 lean-4.28-reap 的 bind mount 做法） |
| 环境变量 | `ELAN_HOME=/mnt/gloway/projects/lean-4.28-reap/elan`；项目目录 `.../lean-4.28-reap/runtime` |
| 版本标签 | 每条记录带 `lean_version / mathlib_rev / reap_commit / patchset_hash / policy_version / config_hash` |
| 数据安全 | 凭据只从环境变量或 600 权限文件读；不进日志 / 回执 |

## 4. 成功标准（v1 验收）

| # | 标准 | 验证方式 |
|---|---|---|
| S1 | Pell 课程第一课 solved | 新链路跑 `PellInvariantSmoke`，对照现有 `sessions-invariant2`（`solved: true`） |
| S2 | 对齐语义回归零失败 | 仓库既有测试（`tests/test_mcts_parity.py` 等）+ 新增迭代版对照全绿 |
| S3 | 并发稳定 | N≥4 会话连续跑 ≥2h；kill -9 一个 worker 后看门狗 ≤5s 恢复 |
| S4 | 训练导出闭环 | 一波（≥20 题）导出 shard → `run_expert_iteration` 冒烟成功 |
| S5 | 基准报告 | `lmc bench` 产出 apply p50/p95、RSS/会话、建议 N（写 `bench-report.json`） |

## 5. 干系路径

- 用户（WSL）—SSH→ my-new-linux（控制 / 查看 / 启动）；
- my-new-linux —HTTP（出站）→ GPU 推理服务（policy/value）；
- my-new-linux —rsync/scp（出站）→ GPU 训练侧（shard 交付，或由用户编排搬运）。

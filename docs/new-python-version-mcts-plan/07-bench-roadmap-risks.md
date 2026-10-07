# 07 · 基准、里程碑与风险

## 1. D0 基准（第一步执行）

| 项目 | 方法 | 通过线 |
|---|---|---|
| 会话冷启动（加载 Mathlib） | 起会话到 `open` 完成 | 记录实测（预计 5–20s，预热可摊薄） |
| 单 apply p50/p95（warm） | 标准题上 100 次 apply | p50 ≤ 500ms（目标 ≤100ms） |
| goals 获取延迟 | 同上 | < 50ms（缓存后 ~µs 级） |
| RSS / 会话 | `/proc/<pid>/status` VmRSS | 记录；据此定 N |
| N 扩展曲线 | N=2/4/6/8 吞吐与稳定性 | 近似线性到内存上限前 |
| 严格编译耗时 | 一题整文件编译 | 记录（预计 10–60s） |

- 产出：`bench-report.json` + 人读摘要（`lmc bench` 输出）。
- 结论示例字段：`recommended_workers`、`expected_throughput_apply_per_s`。

## 2. 里程碑

| 里程碑 | 范围 | 验收 | 依赖 |
|---|---|---|---|
| **M0** spike + bench | 路线 A/B/C 试验；D0 全量 | 路线决策 + 基准报告 | 03 §6 |
| **M1** walking skeleton | 单会话 + mock GPU + 单题真 Lean | Pell 第一课 solved + 严格复验 | M0 |
| **M2** 池化并发 | N 会话 / K 搜索 / watchdog / CLI status | S3 稳定 2h | M1 |
| **M3** GPU 真服务 | `/eval` 真推理（policy+value） | 3–5 题小波次（含 1 解出） | GPU 侧服务；M2 |
| **M4** 导出闭环 | store → export → update_offline 冒烟 | S4 | M3 |
| **M5** 运维硬化 | systemd / metrics / token / 背压 / 预热 | 24h 无人值守运行 | M4 |

## 3. 开放问题（spike 清单）

| # | 问题 | 解决方式 |
|---|---|---|
| S-1 | 会话实现路线 A/B/C | §03-6 判据；A 首选，2 天不可行转 B |
| S-2 | 状态分身（snapshot / 克隆）成本与隔离性 | A 路线 spike 实测 |
| S-3 | prompt 形态（是否带前提检索 / 缩进原样） | v0 与 v1 mock 对齐（`num_premises=0`）；后续另议 |
| S-4 | `/eval` 单端点 vs 双端点；logprob 字段定义 | 与 GPU 侧定稿（02 §5 为建议） |
| S-5 | 是否需要客户端批处理 | 由 GPU 服务连续批处理能力决定（留 EvalBatcher） |
| S-6 | rc1 与正式版 v4.28.0 的切换路径 | 接口留 `toolchain` 字段；后置 |

## 4. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 路线 A 侵入 Reap 内部失败 | 工期 | 先 spike；B/C 兜底 |
| 句柄 / IPC 往返拖慢搜索 | 吞吐 | 句柄化 + pp 缓存 + 批处理；与 Lean 内 MCTS 对照实测 |
| 每会话 Mathlib 内存过高 | 并发上限 | 实测 RSS；N 降级；必要时裁 import |
| exfat / 权限坑 | 环境故障 | venv / 状态在 ext4；bind mount 方案已验证 |
| 版本漂移（rc1 / final） | 收据不可比 | 版本三元组强校验；后置切换 |
| 失败污染 / 状态泄漏 | 正确性 | 隔离执行 + 与路线 C 对拍 100 例 |

## 5. 参考

- 仓库内：`docs/status.md`、`docs/alignment.md`、`docs/real_lean_next.md`、`lean/pell_smoke/README.md`；
- 数值与风格：`examples/N-02_class-node-exercise.ipynb`、`examples/update_modes_walkthrough.{py,ipynb}`、`examples/00-风格与编号规范.md`；
- v1 资产：`/mnt/gloway/projects/reap-new-update-model/v1-result/20260828-real7b-pell-success/code/`（`cpu_runtime/`、`containers/cpu/*-overlay/`）。

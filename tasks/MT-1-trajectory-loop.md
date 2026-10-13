# MT-1 · 大规模问题 → 轨迹 → 训练闭环

- 状态：进行中（2026-10-09 启动）
- 目标：投放大量问题 → lmc-core 产生轨迹（证明/反证/失败）→ 入池 →
  gpu-serve 训练（**CE 更新为核心，逻辑固定**）→ 迭代提升解出率与泛化。
- 方法变体（放支线，不登记于此）：题目选择、课程学习与变体、预算分配、选择规则、温度/采样策略等。

## 阶段

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| P0 | lmc-core 池子 + gpu-serve CLI 就绪；现有数据迁入；发布（GitHub/HF，公开） | `lmc export` 出 shard+manifest；`gpu-serve smoke` 过 | 进行中 |
| P1 | 小规模端到端：一波（v001 级）→ 训练 → 评测 | 与官方 CE 基线可对比的回执 | 待 P0 |
| P2 | 扩大题量 / 课程化 | 解出率提升的可复现证据 | 待 P1 |

## 固定接口（引用，不重复定义）

- CPU：`lmc`（`docs/new-python-version-mcts-plan/06-cli-and-ops.md`）；store/export 布局（`05`）；Transition schema。
- GPU：`gpu-serve serve-eval | train offline | train online | eval heldout | pool | versions | smoke`。
- `/eval` 契约（`02 §5`）；版本三元组；observer v1 事件。

## 记录

- 2026-10-09：GPU 新实例上线（`dsw-2239033-79dcbd66ff-nkh7x`，gfx942 / ROCm 7.2，torch 可用，模型与仓库副本均在）；体检通过。
- 2026-10-09：派出两条满足准入的支线（后台子智能体）：① `gpu-serve` 参数化 CLI 打包；② trajectory 人类展示渲染器原型。
- 2026-10-09：支线 SUB-2 完成（showcase 渲染器；5 测试通过、全量 31 passed；示例 `docs/showcase/example-pell.html`）。
- 2026-10-09：支线 SUB-1 完成（`gpu_serve` 参数化 CLI；43 passed/7 skipped（基线 venv）、58 passed（torchcpu）；修复 LoRA `is_trainable` 缺陷；云端 smoke 顺延（实例已停，见 T1–T6 恢复流程））。
- 2026-10-09：支线 SUB-4 完成（HF 发布管线）：公开数据集 `alpha-proof-open-source/10-4-alpha-proof-trajectories`（wave_001 首批 5 文件，匿名 200、回读 sha256 一致）；工具 `lean-mcts-cpu-server/scripts/publish_hf.py` + `docs/PUBLISHING.md`；**上传 token 缓存名 `hf-write-token-for-alpha-proof-org`**（旧 `~/.cache/huggingface/token` 的 org 权限为空，勿再用）。
- 2026-10-09：支线 SUB-3 完成（lmc-core 池子）：`lmc/store.py` + CLI（catalog/status/export/versions/verify/search）；真 Lean 端到端落盘 + strict PASS；FATE evidence 迁入 `wave_001_fate_m_v001`（20 题/20 transitions，strict 20/20）；展示 `docs/showcase/fate_m_003.html`；24 tests 通过。
- 2026-10-09：支线 SUB-5 完成（`gpu-serve pool` 四子命令）：public HF 匿名 pull 实测通过（sha256/计数校验、幂等）、merge-logprobs 真实数据 20/20 join；测试 67 passed/7 skipped（alpha104）、82 passed（torchcpu）。
- 2026-10-09：HF 数据对齐（canonical=lmc-store 导出）：wave_001 shard 更新为 store 版（sha256 806793fd…）+ manifest schema lmc.export_manifest.v1；README/catalog 注明；匿名回读校验通过（HF commit 0fd1b9a4）。

# 第三方组件与数据

本仓库自有代码以 Apache-2.0 发布（见 `LICENSE`；Copyright 2026 wufuju2023-cell）。
以下第三方内容为依赖、补丁或随附数据，版权与许可归各自作者所有。

| 组件 | 许可 | 位置 / 说明 |
|---|---|---|
| Reap（IQuestLab/reap） | Apache-2.0 | `lean/patches/0001–0005`：对其源码的补丁（τ/选项、PUCT、失败兜底、canonical candidate provenance、selected-path producer）；完整环境见姊妹仓库 lean-mcts-cpu-server 的 `env/` |
| Lean 4（leanprover/lean4） | Apache-2.0 | 环境依赖 |
| Mathlib4（leanprover-community/mathlib4） | Apache-2.0 | 环境依赖 |
| REAL-Prover（FrenzyMath/REAL-Prover@fe76f68d） | Apache-2.0 | 基座模型（HuggingFace 资产，不随本仓库分发；锁文件见 `assets/model-lock/`） |
| 值头 artifact（`WufuJu/v1-1-fullv3-artifact`、`alpha-proof-open-source/alphaproof-full-v3-value-head`） | Apache-2.0 | HuggingFace 资产，不随本仓库分发 |
| FATE-M 数据集 | MIT（Copyright (c) 2025 FrenzyMath） | `hsy-的分析/`（交付快照与提取物；含两份 `FATE-M-LICENSE.txt`） |
| 轨迹数据集（`alpha-proof-open-source/10-4-alpha-proof-trajectories`） | 见 HuggingFace 数据集卡（当前标注为 other） | 发布于 HuggingFace，不在本仓库内 |
| Pell 题面 | 本仓库验收用例（来源说明见 README） | `assets/pell/` |

本仓库是独立实现（对 AlphaProof 论文语义的对齐复现），与 Google DeepMind 无关联；
「AlphaProof」仅作为所对齐项目的名称引用。

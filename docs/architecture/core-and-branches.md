# Core 分支与契约（lmc-core + gpu-serve）

> 2026-10-09 定稿（用户拍板）。本文是**分支地图与接口基线**：所有支线实验引用本文做对照。
> 主任务台账：`tasks/`（MT-1：大规模轨迹 → 训练闭环）。

## 0. 主线定义（固定）

- **CPU 端（lmc-core）**：MCTS + Lean 后端（会话池、搜索、严格复验、store/export）。
- **GPU 端（gpu-serve）**：参数更新（CE 为核心）、policy/value 推理服务、池子管理。
- 主线之外皆为支线；支线不得修改主线代码（见 §4 准入）。

## 1. 冻结清单（五条，2026-10-09 确认）

1. **CLI 命令面**
   - CPU：`lmc serve|status|search|wave|bench|export|verify|gc|versions|session`
   - GPU：`gpu-serve serve-eval|train offline|train online|eval heldout|pool {pull,push,merge-logprobs,stats}|versions|smoke`
2. **store 布局 + Transition schema + manifest 哈希**（`docs/new-python-version-mcts-plan/05`）。
3. **`/eval` 契约**（`02 §5`；客户端参考实现 `lean-mcts-cpu-server/lmc/eval_client.py`）。
4. **版本三元组强校验**（lean / mathlib / reap + patchset_hash + policy_version）。
5. **事件 schema**（observer v1 兼容，便于与交付证据对拍）。

另：**官方 CE 基线原样冻结**，作为永久对照臂。

## 2. 池子（三层）与批次

```text
CPU 原始池   $LMC_STORE/waves/<wave_id>/...              全量（证明/反证/超时/失败），append-only
CPU 展示位   $LMC_STORE/showcase/ + 仓库 docs/showcase/   人类可读 trajectory（MCTS 过程展示）
GPU 训练池   /mnt/workspace/pool/<wave_id>/shard-*.jsonl + manifest.json
```

- **批次 = wave**（题目集 + 模型版本 + 超参三元组）；`catalog.json` 记录"这批有哪些轨迹"；
- 同步**单向**：CPU export → GPU pool；GPU 侧 `--merge-logprobs` 回填（在线支线用）；
- 发布：**GitHub**（清单/展示/小样本，范围待确认）+ **HuggingFace**（完整分片）——**全部 public**；
  HF 数据集：`alpha-proof-open-source/10-4-alpha-proof-trajectories`（2026-10-09 首批 wave_001 已发布；canonical=lmc-store 导出；上传 token 缓存名 `hf-write-token-for-alpha-proof-org`）；
- 每题附 `frontier` 标记字段（greedy 可解 / 仅 search 可解 / 不可解）供 E8 课程使用。

## 3. 可插拔 vs 核心（gpu-serve）

| 可插拔（参数注入，换组件不改代码） | 核心（冻结，只调参不改逻辑） |
|---|---|
| base model（任意 HF 路径）、LoRA adapter（可空）、value head（hidden size 自适应）、dtype/device | CE 更新逻辑（`update_offline`）、value target 语义、Transition 读写、`/eval` schema、版本校验 |
| 超参：temperature、LoRA rank、lr、batch、sft_mix、β_KL（在线壳）等 |  |

换 base 模型 checklist（如 Qwen3.8-27B）：hidden size、LoRA target modules、tokenizer、价值头重训。

## 4. 支线准入（2026-10-09 用户定）

**准入 = 不新增 CPU 池子 CLI / GPU 池子与打包 CLI 代码 + 不碰 MCTS 算法 + 不改 CE 逻辑。**
不满足者暂缓（OnlineV2、族覆盖、检索/外化等需要主线新功能者）。

在跑支线（后台子智能体）：

| 支线 | 内容 | 产出位置 |
|---|---|---|
| SUB-1 | `gpu-serve` 参数化 CLI 打包（base/head/LoRA 可插拔 + 超参可调） | ✅ 2026-10-09 完成：`gpu_serve/`、`pyproject.toml`、`docs/gpu-serve.md`（云端 smoke 待实例恢复补测） |
| SUB-2 | trajectory 人类展示渲染器原型（单文件 HTML） | ✅ 2026-10-09 完成：`tools/showcase_render.py`、`docs/showcase/example-pell.html` |
| SUB-3 | lmc-core 池子 store + CLI + evidence 迁移 + 展示位接入 | ✅ 2026-10-09 完成：`lmc/store.py`、CLI 6 子命令、`wave_001_fate_m_v001`（20/20 strict）、24 tests |
| SUB-4 | HF 发布管线（public 数据集 + 可复用工具） | ✅ 2026-10-09 完成：数据集 `alpha-proof-open-source/10-4-alpha-proof-trajectories`、`scripts/publish_hf.py` |
| SUB-5 | `gpu-serve pool`（pull/push/merge-logprobs/stats） | ✅ 2026-10-09 完成：池上线（`gpu_serve/pool.py`，24 测试） |

已判暂缓：OnlineV2（改核心）、MCTS 算法改动、E4/E7 等需主线新功能者。

## 5. 相关文档

- 服务器规格：`docs/new-python-version-mcts-plan/{02,05,06}.md`
- 实验计划：`docs/diversity-mcts/3-实验计划.md`；GPU 队列：`docs/diversity-mcts/7-GPU待办清单.md`
- 主任务：`tasks/`；冻结前分析：`docs/diversity-mcts/{5-HTPS-对照笔记.md,6-实验总览.md}`

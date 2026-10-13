# tasks/ · 主线任务台账

> 主线（固定，2026-10-09 定）：**lmc-core（CPU）+ gpu-serve（GPU）**。
> CPU 核心 = MCTS + Lean 后端；GPU 核心 = 参数更新、CE 方法、池子管理。
> 本目录只登记**主线任务**；支线实验按"准入规则"执行，不登记于此。

## 主任务

| 编号 | 任务 | 状态 | 明细 |
|---|---|---|---|
| MT-1 | 大规模问题 → 轨迹 → 训练闭环（提升模型性能） | 进行中 | [MT-1-trajectory-loop.md](MT-1-trajectory-loop.md) |

## 支线准入规则（2026-10-09 定）

支线实验**准入**须同时满足：

1. 不新增 CPU 池子 CLI / GPU 池子与打包 CLI 的代码；
2. 不触碰 MCTS 算法逻辑；
3. 不改变 CE 更新逻辑（只允许调参、换模型组件参数）。

不满足者（OnlineV2、族覆盖、检索/外化等需要主线新功能者）暂缓，随主线升级再议。
满足者可派后台子智能体执行；结果落 `docs/diversity-mcts/reports/` 或独立位置。

## 相关

- 冻结契约 / 分支地图：`docs/architecture/core-and-branches.md`
- 实验计划：`docs/diversity-mcts/3-实验计划.md`
- GPU 恢复清单：`docs/diversity-mcts/7-GPU待办清单.md`

# assets/pell · 测试题面

单道佩尔题（Pell）用于“跑通流程”的第一道冒烟题。

- **题面**：任意给定自然数 B，存在自然数 x、y，使 x > B、y > B 且 x² = 2y² + 1。
- **来源**：用户已验收实验 `20260828-real7b-pell-success`
  （`reap-new-update-model/v1-result/20260828-real7b-pell-success/`）。
  本目录只摘录题面与固定变换，不含完整证明包；完整证明与验收回执见原实验目录。
- **版权说明**：这是一道经典 Pell 方程练习，不声称原创；本仓库仅作测试用例。
- **Lean 声明**：见 `target.lean`（`CodexMathFive.Pell.Target`）。

对照实验中的七门课程与完整证明：

| 课程 | 内容（简述） |
| --- | --- |
| 1 Invariant | 变换 T(x,y)=(3x+4y, 2x+3y) 保持方程 |
| 2 Growth | 正数时两坐标严格增长 |
| 3–7 | 序列构造、归纳、存在见证等（详见原实验 `01-problem-and-curriculum.md`） |

Mock 冒烟使用的战术骨架：`intro B` → `refine <17, 12, ?_, ?_>`（产生 2 个子目标，AND 节点）→ 两支 `norm_num`。

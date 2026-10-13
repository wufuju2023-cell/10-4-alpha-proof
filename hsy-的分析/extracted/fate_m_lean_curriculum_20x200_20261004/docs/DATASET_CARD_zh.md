# 数据卡：FATE-M Lean Curriculum 20×200

## 目的

本数据集把 20 个 FATE-M 基础代数问题扩展成 4000 个 Lean 4 证明任务，用于课程学习、逐级推理训练、推理管线烟雾测试和严格 Lean 验证。它不是新数学定理集合；核心数学目标来自 FATE-M，变化主要发生在逻辑脚手架和附加证明义务上。

## 数据单元

每条记录是一段完整 Lean 源码：

- `import Mathlib`；
- 必要的 `open` 声明；
- `namespace FateCurriculum`；
- 一个唯一命名的 `theorem`；
- 唯一一个 `sorry`，表示待模型或证明器完成的证明体。

独立文件与 `data/problems.jsonl` 的 `formal_statement` 字段逐字一致，其 SHA-256 记录在 `sha256` 字段和 CSV 清单中。

## JSONL 字段

| 字段 | 含义 |
|---|---|
| `id` | 全局唯一题号，例如 `fate_m_003_v176` |
| `family_index` | 本交付中的母题序号 1–20 |
| `fate_id` | 上游 FATE-M 原题 ID |
| `variant_index` | 家族内顺序 1–200 |
| `difficulty_rank` | 与 `variant_index` 相同的课程排序值 |
| `difficulty_tier` | 六级难度层级 1–6 |
| `transformation` | 使用的逻辑变换名称 |
| `transformation_depth` | 当前层级内的结构深度 |
| `theorem_name` | Lean 定理名 |
| `formal_statement` | 可独立加载的完整 Lean 源码 |
| `informal_statement` | 上游 FATE-M 自然语言陈述 |
| `source_commit` | 固定的 FATE-M Git 提交 |
| `source_theorem_name` | 上游 Lean 定理名 |
| `sha256` | `formal_statement` UTF-8 字节哈希 |

## 难度协议

难度按“移除脚手架、增加逻辑路径、最终要求原始数学证明”的原则排序。

1. `direct_target`：目标作为假设直接提供；难点主要是识别目标并忽略上下文。
2. `packed_target`：目标位于嵌套合取末端；需要逐层投影。
3. `implication_chain`：需要将给定事实依次应用于蕴含链。
4. `disjunction_elimination`：需要处理目标分支并消去含 `False` 的分支。
5. `iff_bridge`：需要从末端事实沿等价链反向返回目标。
6. `unscaffolded`：不再给目标证明。`v176` 与原 FATE 形式目标一致（仅定理名和命名空间改变），`v177`–`v200` 还要求完成额外真合取。

排序只保证生成协议上的非递减课程结构，不声称相邻两个 rank 对所有模型都有严格递增的实测难度。若用于论文或模型比较，应单独测量成功率、证明长度与时间，并报告层级而不是把 rank 当作等距量表。

## 数据完整性

`scripts/validate_dataset.py` 检查：

- 恰好 20 个家族、每家族恰好 200 条；
- 4000 个 ID 唯一；
- 4000 份完整源码字节哈希唯一；
- 每条恰好一个 `import Mathlib` 和一个 `sorry`；
- 独立 `.lean` 文件与 JSONL 逐字一致；
- 20 个批量文件各含 200 个定理和 200 个证明洞。

`scripts/compile_families.py` 用 `lake env lean` 编译 20 个批量文件。当前报告位于 `build/lean-validation/report.json`，结果为 20/20 通过，覆盖 4000 个声明。Lean 接受 `sorry`，所以这里验证的是导入、语法、名称解析、类型与定理陈述的良构性，不是证明已完成。

## 已知边界

- 变体是逻辑课程化派生，不是 4000 个彼此独立采集的数学命题。
- 前 175 个变体含显式证明脚手架，不适合作为原始 FATE 难度的评测样本。
- `v176` 是与上游目标最接近的基准点；比较正式 FATE 成绩时仍应使用官方仓库。
- 生成器使用闭合的自然数自等式作为可判定的真命题标签，以保证变体确定、唯一且不引入额外数学依赖。
- 数据不含参考证明或答案，只有 `sorry` 证明洞。

## 来源、版本与许可

选择与哈希见 `config/selection.json`。上游 FATE-M 采用 MIT License；许可副本见 `FATE-M-LICENSE.txt`。发布或再分发时应保留上游版权与许可说明，并明确这是派生数据。

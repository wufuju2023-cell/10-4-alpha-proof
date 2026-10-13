# FATE-M Lean Curriculum 20×200

本目录交付一套从 FATE-M 选出的 20 个代数母题，以及每题 200 个按课程难度排序的 Lean 4 变体，共 **4000 个 Lean 问题**。

## 当前状态

- 状态：完成。
- 数量：20 个家族 × 200 个变体 = 4000。
- 形式：每条都是可独立加载的 Lean 4 源码，证明位置保留唯一一个 `sorry`。
- 工具链：Lean `v4.28.0`、Mathlib `v4.28.0`。
- 校验：20/20 个批量文件编译通过，覆盖 4000 个定理声明；结构校验确认 4000 个唯一 ID、4000 份唯一源码。
- 权威数据：`data/problems.jsonl`；人类浏览优先看 `lean/problems/`。

## 交付目录

| 路径 | 内容 |
|---|---|
| `lean/problems/` | 4000 个独立 `.lean` 文件，按 20 个家族分目录 |
| `lean/families/` | 20 个批量 `.lean` 文件，每个包含 200 个定理，适合整体验证 |
| `data/problems.jsonl` | 4000 条机器可读记录；`formal_statement` 是完整 Lean 源码 |
| `data/manifest.csv` | 轻量索引、难度层级、来源和 SHA-256 |
| `data/originals.json` | 20 个固定的 FATE-M 原题快照 |
| `config/selection.json` | 选题、来源提交、哈希和六级生成协议 |
| `scripts/` | 来源抓取、确定性生成、结构校验、Lean 编译脚本 |
| `build/lean-validation/report.json` | 本次 20/20 编译通过的机器可读报告 |
| `docs/DATASET_CARD_zh.md` | 设计、字段、使用边界与验证说明 |

## 20 个母题

| 家族 | FATE-M ID | 原 Lean 定理名 | 主题 |
|---|---:|---|---|
| P01 | 3 | `comp_surjective_of_surjective` | 满同态的复合仍满射 |
| P02 | 4 | `mul_mem_ker_comm` | 乘积交换次序与核成员关系 |
| P03 | 9 | `orderOf_eq_zero_of_monoidHom` | 同态像的无限阶反推原像无限阶 |
| P04 | 11 | `mul_sub_and_sub_mul` | 环中的左右分配律 |
| P05 | 14 | `zero_of_pow_eq_zero` | 整环中幂为零推出元素为零 |
| P06 | 15 | `left_right_inverse_eq` | 左逆与右逆相等 |
| P07 | 18 | `Ideal.add_le_of_le` | 两理想之和的包含关系 |
| P08 | 21 | `existUnique_linear_solution` | 域上线性方程的唯一解 |
| P09 | 35 | `conj_fixedPoint_eq` | 复共轭不动点与实数 |
| P10 | 40 | `int_eq_five_seven_span` | 5 与 7 生成整数 |
| P11 | 48 | `relations_of_relations` | 群关系的逆元变换 |
| P12 | 51 | `mul_comm_of_relation` | 平方关系推出交换 |
| P13 | 52 | `mul_mul_eq_one` | 群乘积等于单位元的循环移位 |
| P14 | 61 | `Subgroup.inf_normal_of_normal` | 正规子群的交仍正规 |
| P15 | 64 | `existUnique_solution` | 群中左右乘方程的唯一解 |
| P16 | 65 | `existUnique_inverse` | 群元素逆元的存在唯一性 |
| P17 | 66 | `existUnique_mul_eq` | 三项乘法方程的唯一解 |
| P18 | 67 | `MonoidHom.map_inv_eq_inv_map` | 同态保持逆元 |
| P19 | 68 | `orderOf_inv_eq_orderOf` | 元素与逆元的阶相同 |
| P20 | 76 | `cosets_eq_of_inter_ne_empty` | 有交集的陪集相等 |

选题只来自 FATE-M，没有混入 FATE-H 或 FATE-X。选择标准是：教材级、形式陈述紧凑、使用标准 Mathlib 概念、覆盖多种基础代数主题。

## 难度顺序

每个家族内 `v001` 到 `v200` 按同一协议排列：

| 变体范围 | 层级 | 结构 |
|---|---:|---|
| 001–040 | 1 | 直接给出目标；逐步增加无关真命题上下文 |
| 041–080 | 2 | 从逐步加深的合取中提取目标 |
| 081–120 | 3 | 对逐步加长的蕴含链应用已给事实 |
| 121–160 | 4 | 从含越来越多不可能分支的析取中恢复目标 |
| 161–175 | 5 | 反向穿过逐步加长的等价链得到目标 |
| 176–200 | 6 | 不再提供目标证明；176 是原始 FATE 目标，177–200 在原目标后增加真合取义务 |

这是**结构化课程顺序**，不是经过人类或模型成功率标定的绝对难度刻度。前 175 题用于脚手架学习，176–200 才要求真正完成原 FATE 数学核心。

## 使用

单题示例：

```text
lean/problems/P01_FATE_M_003/v001.lean
lean/problems/P01_FATE_M_003/v176.lean
lean/problems/P01_FATE_M_003/v200.lean
```

JSONL 中常用字段：`id`、`fate_id`、`variant_index`、`difficulty_tier`、`transformation`、`formal_statement`、`sha256`。

## 复现与校验

在本目录执行：

```powershell
python scripts\fetch_selected_originals.py
python scripts\generate_variants.py
python scripts\validate_dataset.py
lake update
lake exe cache get
python scripts\compile_families.py
```

前三条命令重新抓取固定提交、验证上游 SHA-256、生成 4000 条数据并做结构检查；后三条准备固定 Lean 环境并编译全部 20 个家族。

## 来源与许可

- 上游：<https://github.com/frenzymath/FATE> / <https://github.com/frenzymath/FATE-M>
- FATE 总仓库提交：`bb646ecb9b68942ebf4bac31ccf4218a58ef9771`
- FATE-M 提交：`4eb33c8ccd0ff058b461cd763cc406509129743f`
- 上游许可：MIT，副本见 `docs/FATE-M-LICENSE.txt`。

本交付是 FATE-M 陈述的派生课程数据，不是 FATE 官方新增 benchmark，也不应把前 175 个带脚手架变体当作原始 FATE 成绩进行比较。

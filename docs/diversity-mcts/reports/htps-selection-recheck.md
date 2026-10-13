# HTPS 式"最小证明选择"重排实验报告

- 日期：2026-10-08
- 状态：完成（纯 CPU / 0 GPU；单波历史数据离线重放）
- 数据源：`training_initial_evidence_20261006.zip`（与 E2 同源；20 题 / 1280 draw / 49 条 verified）
- 脚本：`scripts/htps_selection_recheck.py`
- 统计产物：`reports/htps-selection-recheck.json`
- 口径：B = 64 draw/题；k = canonical 战术在 64 次采样中的命中次数；重发现概率 ≈ `1-(1-k/64)^64`

## 0. 结论速览

| 规则 | Σk（20 题） | 平均 k | 选中 k=1 的题数 | 选中 k≤2 的题数 | 平均重发现概率 |
|---|---:|---:|---:|---:|---:|
| **R0 first**（现状：评估顺序最早通过验证） | 173 | 8.65 | **9** | **11** | **0.823** |
| **R1 maxk**（最高频变体） | **258** | **12.90** | **1** | **2** | **0.975** |
| **R2 mintok**（战术 token 数最少，"最小证明"代理） | 236 | 11.80 | 3 | 4 | 0.938 |
| R3 minchar（字符数最少，稳健性对照） | 219 | 10.95 | 4 | 5 | 0.919 |
| RAND（均匀随机，解析期望） | 206.1 | 10.30 | 4.92 | — | 0.894 |

1. **现状规则 R0 是所有合理候选里最差的**：平均重发现概率 0.823，**甚至低于均匀随机**（0.894）。
2. **机制已定位**：E2 已证 canonical 评估顺序 ≈ survivor 样本序号降序；而 survivor 序号与 k 强负相关（Spearman = **-0.803**）。于是"第一个通过验证"结构性偏爱稀有变体（选中者的 survivor 分位恒为 **1.00**，maxk 规则仅 0.15）。
3. **离线换规则即有大改善**：改 max-k 后，选中 k=1 的题数 9 → 1，平均重发现 0.823 → 0.975；改"最短战术"后 9 → 3（0.938），且选中动作平均更短。注意这只是**目标选择**视角的收益，正式对训练吸收的影响需下一波 A/B 验证。

## 1. 方法

对每题已验证（verified_proof）的 canonical 候选集合重放五种选择规则：

```text
R0 first   : min(sequence)                        —— 现状（E2 §3 观测规则）
R1 maxk    : max(k)，tie 用较早 sequence          —— 最高频
R2 mintok  : min(token 数)，tie 用 max(k)、再 sequence —— HTPS"最小证明"代理
R3 minchar : min(字符数)，tie 用 max(k)、再 sequence —— 稳健性对照
RAND       : 均匀随机（解析期望：E[k] = 均值，P(k=1) = 比例）
```

- **token 数**取自 `canonical_candidate.sample_tactic_token_spans` 中 survivor 样本的 `[start,end)` 跨度；
- 本批全部为 tier-1 单步题（每题一个单步解），所以"最小证明树"的步数判据退化，用**战术长度**作代理；真实 Lean 执行耗时本批未按候选记录（见 §6）；
- "多解题"= 该题 verified 候选数 ≥ 2；本次 20 题中 **9 题**为多解题（规则只在多解题上产生差异）。
- 指标：Σk、平均 k、k=1/k≤2 题数、平均重发现、规则间动作差异数；token 长度与 k 的 Spearman 秩相关。

## 2. 总体结果

见 §0 表。补充：

- 规则间动作差异（共 20 题）：`first≠maxk: 9`；`first≠mintok: 10`；`first≠minchar: 9`；`maxk≠mintok: 4`。
- 平均重发现按题平均，与 Σk 的排序一致：`maxk (0.975) > mintok (0.938) > minchar (0.919) > RAND (0.894) > first (0.823)`。

## 3. 机制：为什么 first-verified 会偏向稀有变体

链条（每步都有本批数据支撑）：

```text
① E2 §3：canonical 的评估顺序 = survivor 样本序号降序（后出现者先被评估）
② "first-verified" = 评估顺序上第一个通过验证 → 恒为 survivor 最大的 verified 变体
   （本实验复核：R0 选中者 survivor 分位 = 1.00；R1 仅 0.15）
③ survivor = 组内最早出现的样本序号；越稀有的战术，期望首现位置越靠后
   （本实验：Spearman(survivor, k) = -0.803）
⇒ first-verified 结构性把"最稀有"的已验证变体选作 CE 目标
```

这也解释了为什么 R0 连均匀随机都不如（随机期望 0.894 > 0.823）：它不是无偏，而是**反向偏置**。

## 4. 多解题逐题对照（9 题）

| problem | n_ver | R0 first | R1 maxk | R2 mintok |
|---|---:|---|---|---|
| fate_m_009_v001 | 4 | k=1 t=14 `simp only [curriculum_target, Nat.zero_add, zero_add]` | k=5 t=3 `exact curriculum_target` | k=5 t=3 `exact curriculum_target` |
| fate_m_011_v001 | 3 | k=1 t=38 `exact (curriculum_target : ...)` | k=5 t=3 `exact curriculum_target` | k=5 t=3 `exact curriculum_target` |
| fate_m_014_v001 | 5 | k=1 t=19 `simp only [curriculum_target, eq, zero_pow ...]` | k=11 t=2 `assumption` | k=11 t=2 `assumption` |
| fate_m_018_v001 | 3 | k=1 t=3 `apply curriculum_target` | k=10 t=3 `exact curriculum_target` | k=1 t=2 `assumption` |
| fate_m_040_v001 | 9 | k=1 t=8 `exact fun z ↦ by tauto` | k=8 t=8 `exact fun z ↦ curriculum_target z` | k=1 t=3 `exact curriculum_target` |
| fate_m_051_v001 | 2 | k=2 t=3 `apply curriculum_target` | k=16 t=3 `exact curriculum_target` | k=16 t=3 `exact curriculum_target` |
| fate_m_052_v001 | 4 | k=1 t=7 `simp only [curriculum_target]` | k=17 t=3 `exact curriculum_target` | k=17 t=3 `exact curriculum_target` |
| fate_m_061_v001 | 3 | k=1 t=3 `apply curriculum_target` | k=12 t=3 `exact curriculum_target` | k=6 t=2 `assumption` |
| fate_m_068_v001 | 2 | k=1 t=3 `apply curriculum_target` | k=11 t=3 `exact curriculum_target` | k=11 t=3 `exact curriculum_target` |
| fate_m_076_v001 | 4 | k=1 t=9 `exact fun h ↦ curriculum_target h` | k=1 t=9（全部并列 k=1） | k=1 t=3 `exact curriculum_target` |

（t = token 数；f076 是唯一 R1=R0 的题，因为它的 4 个已验证变体 k 全为 1。）

**R0 → R2 的具体动作变化（10 题，完整列表见 JSON）**：典型如
f009 `simp only [curriculum_target, Nat.zero_add, zero_add]`（k=1, 14 tok）→ `exact curriculum_target`（k=5, 3 tok）；
f052 `simp only [curriculum_target]`（k=1, 7 tok）→ `exact curriculum_target`（k=17, 3 tok）。

注意 R2 也有代价：它在 f018/f040 选中了 `assumption` / `exact curriculum_target`（k=1 的短变体）。
"最短"与"最高频"在 4/9 题上不一致——这正是 HTPS 的取舍（他们用最小性换取更短/更便宜的证明，并用在线训练补偿稀有目标）。

## 5. token 长度与 k

- verified 集合内 Spearman(tok_len, k) = **-0.517**（越短越常见，中等强度）；
- verified 平均 token 数 7.47，invalid 平均 6.20——**更短 ≠ 更好**：错误拼写（如 `exact curry_target`，3 tok）又短又高频但全失败。
- 所以"最小化"必须在**已验证集合内**使用（R2 的口径），不能像先验排序那样跨全体使用。
- 与 HTPS 的对应：HTPS-Lean 用"战术总 CPU 时间最小"（并报告超时率降 40%）；我们单步题只能用 token 长度代理；**多步波次应升级为"总步数 / 总 CPU 时间"**。

## 6. E5 数据缺口（重要发现）

检查全部 20 题的 `raw_tree.json` / observer `checkpoint.tree`：

```text
节点数 = 2（根 + 解出子节点）；root children = 1；
所有 edge numVisit = 0；focus 边 = 0 条。
```

→ 本批证据**只保留最优解出路径**，无法恢复"访问分布/软目标"（E5 的核心输入）。
附注：edge 里的 `probability` 字段存在但语义待确认（部分值 >1，疑似未归一 prior 或未除 totalMass），需要服务侧核对。

**建议为 E5 增加的最小导出清单**（runner/actor 侧）：

1. 完整搜索树（含未解、失败、部分展开节点），每节点：`state_key`、`isSolved / isInvalid`、`numVisit`、`valueSum`、`toPlay`；
2. 每条 edge：`tacticStr`、`numVisit`、`W`、`Q`、PUCT 使用的 `prior` 与分量（U/c/score）、`isFocus`；
3. 每个候选（canonical）：执行结果、失败类别（parse/eval/timeout）、`raw_logprob`、**Lean 执行耗时（µs）**；
4. 每次扩张：候选数、采样参数（temperature/top_p/seed）、扩展时的模型版本；
5. 每题：attempt 次数、各 attempt 的超参与结果。

## 7. 建议（按优先级）

1. **最小改动**：CE 目标选择从"first-verified"改为"max-k（tie 用 token 长度或顺序）"——离线指标改善最大（k=1 选中数 9→1，平均重发现 0.823→0.975），且不引入新机制；
2. **对照臂**：在 E5 的 A/B 中同测 `maxk`、`mintok`、软目标（访问分布）三组目标；若 HTPS 结论可迁移，"最短 + 在线补偿"也应被检验；
3. **数据导出**：落实 §6 清单后再做 E5 正式实验（当前证据不可行）；
4. **多步波次**：把最小性判据升级为总步数/总 CPU 时间，并记录逐候选耗时。

## 8. 复现

```bash
cd ~/e2-work                                  # 数据同 E2（自动解压见 02 笔记本）
python3 htps_selection_recheck.py             # → htps-selection-recheck.json
# 脚本已收录：scripts/htps_selection_recheck.py
```

## 9. 局限

- 单波、20 题、全部 tier-1 单步；结论不外推到多步搜索；
- 规则差异只在 9 个多解题上体现；
- "重发现概率"是 k 的 MLE 代理，不是最终解出率；**换选择规则的训练收益必须用新一波实验验证**；
- RAND 为解析期望，非某次具体抽样。

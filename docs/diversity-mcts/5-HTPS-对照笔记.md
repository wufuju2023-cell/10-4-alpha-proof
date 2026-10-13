# 5 · HTPS 对照笔记（arXiv 2205.11491）

> 论文：**HyperTree Proof Search for Neural Theorem Proving**（HTPS），Meta AI，2022-05（v1），NeurIPS 2022。
> 链接：https://arxiv.org/abs/2205.11491 ｜ 模型名：Evariste。
> 本笔记回答：它和我们的架构（AlphaProof 系 CE 主线 + Reap 系 MCTS + 自研 Online-v2）有哪些区别，
> 以及哪些结论可以直接搬、哪些不能；代码/权重的开源情况见 §7。
> 配套：CPU 实验见 `reports/htps-selection-recheck.md`。

## 1. 论文速览（只记要点）

- **环境**：Metamath（set.mm）、**Lean v3.3.0 + mathlib**、自制合成环境 Equations（原型/消融用）。
- **模型**：600M 参数 encoder-decoder（12 层 enc / 6 层 dec；BPE；无检索），从无监督预训练 + 数学监督微调起步。
- **搜索（HTPS）**：把证明过程建模为**超图**（一条战术边指向一组子目标）。一次 simulation：
  1. **选择**：沿搜索策略从根铺出一棵"部分证明树"，直到若干未展开叶子（用虚拟损失批量跑多次选择）；
  2. **扩张**：对**整棵树的所有叶子**批量采样战术并执行（示例 B=2 个/叶；实际每节点样本数每次尝试从 {8,16,32,48} 随机取）；
  3. **回传**：解出叶=1、无效叶=0、其余用 critic 估值；**父值 = 子值乘积**（独立性假设）；更新 N、W，Q=W/N。
- **训练（online）**：
  - 策略目标：seq2seq 交叉熵，样本 = 成功搜索的**最小证明树**（Lean 按**战术总 CPU 时间最小**；Metamath/Equations 按步数最小）；
  - critic 目标：二分类（解码特判 token `PROVABLE`/`UNPROVABLE`）；样本 = solved(1) / invalid(0) / **内部节点（目标=最优战术 Q）**；
  - 分布式异步：controller + 200 provers（各 48 CPU 核）+ 32 trainers（A100）；样本流式入有限队列（满则丢最旧）；provers 每 5 分钟拉新模型；持续混采监督数据。
- **主要结果**：set.mm held-out **65.4% → 82.6%**（在线训练）；Lean **miniF2F-valid 58.6%**、**miniF2F-curriculum 31% → 42%**；miniF2F-test pass@64 41% vs GPT-f 36.6%；7 天解出 139/327 课程题（GPT-f 100），约 10× 训练提速。
- **关键消融**：
  - 策略样本：**minimal proofs ≫ all proofs**（Equations 78.1% vs 40.6%；Metamath 只学根的最小树 pass@8 68.6%）；
  - critic：**软 critic ≫ 硬 critic**（Equations 78.1% vs 63.1%；硬 critic 甚至**不如没有 critic**）；
  - 搜参随机化：+4.3%（Equations）；
  - 在线刷新频率：**5min > 1h > 6h > 不刷新**（"online 优于 expert iteration"的主要证据）。

## 2. 先消歧义：两条不同的"online 轴"

```text
HTPS 的 "online"  = 数据流在线（异步流水线 + 高频权重刷新），训练目标仍是监督式
HTPS 对比的 "expert iteration" = 分批收集 → 批量重训（我们当前波次式 CE 的形态）
我们的 "Online-v2" = 梯度级在线更新（重要性采样 / KL 约束）——HTPS 没做过这条轴
```

**结论：HTPS 的在线结论支持"把主线 CE 数据管线做新鲜/做细"，不能用来论证 Online-v2 的成败。**
反过来说，HTPS 的"expert iteration vs online"消融，恰好是我们**主线波次粒度实验**的现成参照。

## 3. 对比总表

| 维度 | HTPS（2205.11491） | 我们（现状/主线目标） |
|---|---|---|
| 搜索结构 | 超图；一次 simulation 铺**整棵部分证明树**，批量扩张全部叶子；虚拟损失；父值=子值乘积 | PUCT MCTS（官方直译）：单路径选择 → 单节点扩张（计划 K=6）→ 回传；AND 用 `c_AND=64` 加权 + `focus_goal` 伪动作 |
| 先验/探索 | PUCT 或 Regularized Policy；探索常数、深度惩罚、样本数、温度**每次尝试随机** | τ=200 先验温度（先验被强烈压平）；`c_pen=32` 未访问惩罚；超参固定 |
| 值/critic | P(PROVABLE\|goal)；solved=1、invalid=0、内部=最优战术 Q；在线持续训练 | value head 回归 **−d（剩余步数，两热 64 桶）**；AND 目标取 min；失败信号基本未用于策略 |
| 策略训练数据 | 成功搜索的**最小证明树**（Lean：总 CPU 时间最小） | 每题 **1 条 first-verified** transition（E2：顺序敏感、结构性偏稀有，见配套报告） |
| 训练循环 | 异步在线：200 provers / 32 trainers；5min 刷新；队列丢旧；两目标等权 | 波次 expert iteration：一波 → 一次 CE 更新 → 评测；Online-v2 支线（KL 门被拒） |
| 模型 | 600M encoder-decoder；无检索 | 7B REAL-Prover + LoRA + value head（云端） |
| 环境 | Metamath / Lean 3.3.0 / Equations | Lean 4.28.0-rc1 / FATE-M 合成课程（20×200）/ CPU 会话服务器 |
| 评测/分析 | miniF2F、set.mm；消融驱动 | E2 采样窗审计、E3 同预算对照等；诊断驱动（规模小） |

## 4. 关键差异（按对我们的可操作性排序）

1. **选择规则的"反向偏置"问题（本仓已有实锤）**。
   HTPS 用"最小证明树"选择样本；我们的 first-verified 是**顺序驱动**的。
   配套 CPU 实验（`reports/htps-selection-recheck.md`）显示：
   - first-verified 平均重发现 0.823，**低于均匀随机 0.894**；机制是 survivor 序号与 k 强负相关（ρ=-0.80），而它恒选 survivor 最大者；
   - 改 max-k → k=1 选中数 9→1、平均重发现 0.823→0.975；改最短战术 → 9→3、0.938。
2. **critic 的目标定义是独立变量**。HTPS 的软 critic（内部节点=Q）远好于硬 critic，后者甚至比没有 critic 差。
   我们 value head 是 −d 回归 + 对失败信息利用少——E3 应把"critic 目标怎么定"列为实验因子，而不是只比"有/无值函数"。
3. **在线 = 数据新鲜度**。HTPS 的 5min/1h/6h/none 消融说明刷新粒度本身有价值。
   我们的对应实验应是"波次粒度 / 生成时的模型 staleness"（工程变量），与 KL 门无关。
4. **搜索展开单位不同**。HTPS"整棵部分证明树批量扩张 + 虚拟损失"把 AND 约束编码进搜索结构；
   我们是单路径扩张 + `c_AND`/focus 近似。若后续在多步题上做 E4（族覆盖）或搜索改造，这是一个候选方向。
5. **随机化搜参是零架构成本的多样性来源**（+4.3%）：每尝试随机 K、温度、探索常数、预算。
   直接映射 E1（D_sem/D_fam）与 E4（族配额）的实验因子。
6. **长期路线同源**：HTPS 结尾指出"现有语料的搜索数据长期不够，需要自对弈式造题"——正是我们 E8 课程闭环
   （他们用 Equations 合成环境，我们用 FATE-M）。

## 5. 与 E1–E8 的映射

| 我们的实验 | HTPS 对应物 | 状态/动作 |
|---|---|---|
| **E2 采样窗审计** | 无（HTPS 不做采样概率分析） | 已完成 |
| **选择规则重排**（新） | §7.2.1 minimal proofs 消融 | **本轮已完成**（纯 CPU），结论见配套报告；建议改 runner 选择规则 |
| **E5 软目标 vs 硬目标** | critic 软/硬消融是相邻问题；策略侧他们只比"最小树 vs 全部证明" | 软目标数据本轮不可恢复（树被剪成 2 节点、visits=0）→ 需先加导出（报告 §6 清单） |
| **E3 搜索 vs 采样（值函数）** | 无直接对照（他们比"在线 vs 固定模型"） | 保持原计划；把 critic 目标定义加入变量 |
| **E1/E4 多样性/族覆盖** | 搜参随机化 + 虚拟损失批量扩张 | 可先做"随机化搜参"的离线设计，GPU 实验后置 |
| **E8 课程闭环** | 合成环境 Equations + "需要自动造题"结论 | 方向一致，长期 |
| 波次粒度/刷新（新） | §7.2.4 刷新频率消融 | 待管线改造，工程变量 |

## 6. HTPS 结果数字摘录（引用时注意不可比）

- set.mm：65.4%（监督模型 + HTPS 搜索）→ 82.6%（在线训练）；
- Lean：miniF2F-valid 58.6%；miniF2F-curriculum 31% → 42%；test pass@64 41%；
- 训练规模：Lean 上 200 provers × 48 核 + 32 trainers，以"A100 天"计；每道课程题平均 ~250 次尝试；
- 与他们同时代的 GPT-f 相比约 10× 训练提速。

**不可比声明**：HTPS 是 2022 年 Lean 3 生态 + 600M 模型 + 大规模分布式；我们是 Lean 4 + 7B LoRA + 单机 CPU 搜索。
上面的数字用于理解"结论方向"，不能直接与我们的 FATE-M 结果对照。

## 7. 开源情况（代码 / 权重 / 许可；核查于 2026-10-08）

### 7.1 代码：开源，但属"归档性质"

- 官方仓库：`facebookresearch/Evariste`（2024-08-01 建立；CHANGELOG 显示 2024-07-19 初始提交——论文 2022 年发表，约两年后才归档放出）。
- README 原话：**"For archival purposes — this code does NOT run out of the box as we removed references to Meta-internal systems without fixing up entry points for generic setups."**
  即：Meta 内部系统引用被删除、未补通用入口；官方定位是"论文 artifact 与参考"。
- 规模与构成（817 文件 / 约 2.1MB 源码）：
  - `formal/evariste/`（508 文件）：backward prover（HTPS 本体）、distributed MCTS、模型与数据管线、环境（Equations / Lean / Metamath / HOL-Light / Coq）；
  - `lean_fork`（Lean 3 时代补丁，含 leanml）、`proof_explore`（前端可视化）、`lean_imo_th`、`docs/`（含 `formal/backward_prover`、`distributed_mcts` 等实现说明）。
- 许可：**主体 CC-BY-NC 4.0（非商业）**；例外：`lean_fork`（含 leanml）、`lean_proof_recoding`、`Lean Gym` 为 Apache 2.0。
  研究参考可以；产品化不能直接复用 NC 部分。

### 7.2 模型权重：没有开源

- GitHub Releases 为空；仓库内无任何权重文件（无 `.pt/.bin/.safetensors`；`checkpoints.py` 只是存取代码）；
- 仓库总共约 2.1MB（600M 参数权重至少 >1GB）；Setup 文档与 README 均无下载链接；
- HuggingFace 搜索 "evariste" 只有无关个人账号（`EvaristeL/*`）；Meta 官方 HF org 无 prover 模型（仅见无关的 `autores/htps_*` 个人上传）。

### 7.3 替代选项（若目标是"跑起来对照"）

| 资源 | 开源 | 许可 | 能跑 | 备注 |
|---|---|---|---|---|
| 官方 Evariste 代码 | ✅ | CC-BY-NC 4.0（部分 Apache 2.0） | ❌ | 归档，需自行补入口 |
| 官方 Evariste 权重 | ❌ | — | — | Releases/HF/文档均无 |
| `Kripner/nanoproof`（社区） | ✅ | MIT | ✅ | nanochat + AlphaProof 伪代码 + HTPS 风格 MCTS；自称 miniF2F-Valid 52.7%（512 sims）；非官方，质量需自评 |
| LeanDojo / ReProver 生态 | ✅ | 见仓库 | ✅ | 若只要"可跑的开放基线"，可不追求 HTPS 本尊 |

### 7.4 对本仓的含义

1. 不存在"用官方权重初始化/对齐"的路线——继续 REAL-Prover 7B + LoRA 自研对齐；
2. Evariste 代码可作**只读参考**（HTPS 搜索、分布式 MCTS 工程、虚拟损失/批量展开写法）；注意 Lean 3 时代 + NC 许可；
3. 若要读实现，优先看 `docs/formal/distributed_mcts.rst` 与 `formal/evariste/backward*`。

> 核查渠道：GitHub API（仓库元数据 / contents / releases / git tree）与仓库 README、LICENSE 原文；HuggingFace API 搜索。

## 8. 参考

- 论文 HTML：https://arxiv.org/html/2205.11491v1 ；PDF：https://arxiv.org/pdf/2205.11491v1
- 本仓相关：`2-理论-多样性-可达性-吸收.md`（采样窗）、`3-实验计划.md`（E1–E8）、
  `reports/e2-window-audit.md`（顺序敏感的选择规则）、`reports/htps-selection-recheck.md`（本轮 CPU 实验）。

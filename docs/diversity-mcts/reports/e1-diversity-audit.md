# E1 降级版 · 历史语义多样性审计

- 日期：2026-10-08
- 状态：完成（**无 GPU 降级版**；完整版（温度扫描 K=32/64）待 GPU 恢复后按 G4 执行）
- 数据：与 E2 同源（20 题 / 每题 64 采样；zip 哈希见 `reports/e2-window-audit.md`）
- 脚本：`scripts/e1_diversity_audit.py` ｜ 统计：`reports/e1-diversity-audit.json`

## 1. 口径与定义

对每题已有的 64 个历史采样（单波、单一温度 1.5、tier-1 单步题）：

```text
D_sem_hist = #canonical / 64              语义去重后的候选率（近似）
D_raw      = #unique 原始文本 / 64         原始文本多样率
D_fam_hist = #unique 族头 / #canonical     族头 = 动作首个空白分隔 token
top1_share = max(k) / 64                  模态塌缩（最高频变体占比）
entropy    = 频率分布 Shannon 熵（bit）
exec_rate  = 执行候选 / canonical          进展率（经 Lean 解析+执行）
verify_rate= 严格验证成功 / canonical
```

**局限**（对照 `3-实验计划.md` §E1 的完整版）：单温度、单波、tier-1 单步：
`D_sem` 是"canonical 动作去重"近似而**不是真实 `state_key` 去重**；只适合同口径纵向比较。

## 2. 结果（20 题）

聚合（mean [min, max]）：

| 指标 | mean | [min, max] |
|---|---:|---|
| D_sem_hist | **0.182** | [0.062, 0.438] |
| D_raw | 0.182 | [0.062, 0.438] |
| D_fam_hist | 0.456 | [0.045, 0.700] |
| top1_share | **0.530** | [0.219, 0.938] |
| entropy (bit) | 2.174 | [0.431, 4.001] |
| exec_rate | 0.717 | [0.400, 0.955] |
| verify_rate | 0.207 | [0.083, 0.429] |

逐题（表结构：D_sem / top1 / 熵 / exec / verify）：

```text
fate_m_003  0.141  0.688  1.645  0.67  0.11
fate_m_004  0.078  0.812  1.021  0.80  0.20
fate_m_009  0.203  0.516  2.593  0.77  0.31
fate_m_011  0.438  0.219  4.001  0.71  0.11
fate_m_014  0.250  0.391  2.916  0.69  0.31
fate_m_015  0.062  0.562  1.341  0.75  0.25
fate_m_018  0.109  0.750  1.261  0.86  0.43
fate_m_021  0.156  0.547  2.088  0.40  0.10
fate_m_035  0.188  0.344  2.889  0.67  0.08
fate_m_040  0.344  0.250  3.726  0.95  0.41
fate_m_048  0.141  0.594  1.933  0.67  0.11
fate_m_051  0.156  0.516  2.137  0.80  0.20
fate_m_052  0.234  0.500  2.340  0.67  0.27
fate_m_061  0.156  0.500  2.247  0.70  0.30
fate_m_064  0.109  0.578  1.563  0.57  0.14
fate_m_065  0.125  0.500  1.781  0.62  0.12
fate_m_066  0.141  0.422  1.938  0.78  0.11
fate_m_067  0.062  0.938  0.431  0.75  0.25
fate_m_068  0.219  0.516  2.492  0.71  0.14
fate_m_076  0.328  0.453  3.130  0.81  0.19
```

## 3. 观察与判据

1. **语义多样性偏低**：`D_sem_hist` 均值 18.2%（≈ 64 抽样只出 ~12 个不同候选）；
   对照 E1 判据"`D_sem` 低（<30%）→ 多样性是当前瓶颈 → E4 优先级最高"，**降级版支持 E4 进入前列**（注意口径限制）。
2. **模态塌缩明显**：`top1_share` 均值 53%（f067 高达 93.8%）——一半以上的质量压在单一变体上；
   结合 E2 的"高频≠正确"（错误拼写占据模态），这正是"要多样性才有正确候补"的机制。
3. **族头多样性尚可**：`D_fam_hist` 均值 45.6%——动作**头部**（exact/simp/apply/rw…）并不单调，
   多样性损失主要发生在"同一族内的变体选择"（如 curriculum 拼写变体）；
   提示 E4 的族覆盖应按"族内变体"细化，而非只看战术头。
4. `D_raw ≡ D_sem`：本批 canonical 归组与精确文本去重等价（没有额外的近似归并），
   说明 E2 的"语义去重"其实主要是**同文本计数器**；更激进的语义等价类（如 `exact_curriculum_target`
   vs `exact curriculum_target`）**没有被合并**——这是 E1 完整版要修的点。
5. `exec_rate` 71.7%（解析/执行通过率）与 `verify_rate` 20.7%（canonical 层面严格成功率）与 E2 总量一致。

## 4. 复现

```bash
cd /mnt/gloway/projects/10-4-alpha-proof
PYTHONPATH=. ~/venvs/alpha104/bin/python scripts/e1_diversity_audit.py
# → ~/e2-work/e1-diversity-audit.json（已拷贝至 reports/e1-diversity-audit.json）
```

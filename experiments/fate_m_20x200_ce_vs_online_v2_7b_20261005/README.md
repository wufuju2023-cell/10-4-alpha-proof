# REAL-Prover 7B：搜索路径 CE 与 Online-v2

本实验比较同一初始 adapter、同一搜索/严格 Lean 回执下的 CE 与 Online-v2 更新及部署结果。实际只执行 **20 个 family 的 v001、一个共同搜索波**；原 40–160 题、2–8 波自适应协议仍为 **INCOMPLETE**。本页优先于历史诊断记录。

## 当前结果与运行

| 策略 | 严格 heldout 结果 | 证据性质 |
|---|---:|---|
| initial | 24/40，60%，截断0 | 独立40题评测，47.3分钟 |
| corrected CE | 25/40，62.5%，截断0 | 独立40题评测，46.02分钟；原生DONE |
| Online 回滚后部署策略 | 24/40，60% | 与initial策略精确同一，复用initial证据；未独立重跑 |

CE 已完成20条严格 selected-path transition、137个action token、20/20题覆盖；full_replay、batch20、micro1、1次optimizer.step，训练收据16.831秒。Online 消费233个候选（49严格成功、184无效），233/233优势非零，尝试2个epoch，但 **behavior exact KL=0.08703397 > 冻结hard limit0.05**，触发 `post_step_behavior_kl_limit` 并完整回滚，已提交更新数0。训练成本和有效暴露不等，不能宣布泛化的算法优劣。

真实回滚审计PASS：初始与部署adapter权重字节相同；PEFT配置仅target_modules排列顺序不同，语义相同；value head张量、优化器、RNG、beta及scheduler均恢复，ledger为ABORTED。因此Online一栏是部署结果的身份同一性复用，不能解释成Online学习提升。双方接受更新门禁也为INCOMPLETE。

当前远端根：`/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005`，权威运行目录`runs/formal-20x10-corrected/`。CE与终态报告/归档均原生DONE且进程已退出；旧控制器48765/53022已退役，禁止恢复旧诊断链。持久存储最后91.8%，实例剩余约3小时14分；没有仍在运行的实验GPU任务。

CE相对initial净增1题（+2.5个百分点），24题共同成功、15题共同失败、CE独有1题、initial独有0题；新增`fate_m_068_v009`的证明为`exact curriculum_target`。**40题全部是tier1/direct_target并给定curriculum_target假设**，只能描述利用已给目标假设的脚手架表现，不支持数学证明发现能力提升。完整数字、训练/评测成本和边界见[终态报告](derived/results/terminal_report_20261006.md)。

## 权威证据与目录

- `config/`：原子集/自适应协议、单波执行范围说明。160题是冻结可用训练池，不是已执行题数。
- `data/`：固定题集、选择清单及哈希；40题heldout为所有20个family的v009/v010。
- `workstreams/`、`scripts/`：actor、可信verifier join、两臂learner、严格评测、回滚审计和终态报告代码。
- `build/`：可重新生成的上传包；不提交模型或运行缓存。
- `runs/formal-20x10-corrected/final-evidence/`：完整最终ZIP与原样小收据，1475成员校验通过；最终评测权威证据。
- `runs/formal-20x10-corrected/training-evidence/`：已下载训练快照及原样小收据；875个成员哈希验证通过。快照不是最终CE评测证据。
- `runs/formal-20x10/{join-v2,training-initial-evidence}/`：同源原始join、初始评测和先前诊断证据，不原地修改。
- `derived/results/guarded_update_audit_20261006.json`：本地训练/回滚审计，绑定原始ZIP与成员哈希。
- `derived/results/corrected_terminal_comparison/`：CE完成后由远端报告器生成的终态数字和配对分析，明确Online复用。
- `archive/`：有用失败诊断与历史状态；旧CE单样本和旧Online零优势结果不进入当前比较。

训练快照SHA256：`b32f8637c797d5be5354a1f5706879fbbf299780b6199c0cf9c850e0f5e49cb8`。共同join manifest SHA256：`193da051e116bce7b73433bf687d75e3854c82bbbb88818dc175024380c75241`。修复后Online wave SHA256：`ab2cccc55ada03ea1e7298935b5760a9b4bd84b00a256607bd367689c31dcb36`。CE配置SHA256：`e4191e169cc7a7e705b7daace34e337d844b636bffc0bd0b777974953867586f`。更多源码、配置、checkpoint和事务哈希以原始INDEX及审计收据为准。归档中的远端评测器是实际运行源码；PR将等价的模型锁factory抽为具名函数并增加并发回归检查，其文件哈希与远端版本不同，不能当作运行哈希。

## 冻结口径与限制

同一初始REAL-Prover 7B/LoRA、同一v001搜索波：1280原始draw、233唯一执行候选、49严格成功路径，CE选择每题1条成功transition共20条。Online terminal effective-Q只在已绑定search+verifier的可信builder边界派生：proof/disproof +1、invalid -0.1、timeout/infra0，未解决节点保留search Q；不修改签名actor原始回执。

评测固定20个family的相邻v009/v010，共40题；同一prompt、生成参数、每题最多4次/每次512新token、paired seed，首次严格成功后停止。完整Lean独立编译，禁止sorry/admit，截断和timeout单列。heldout未用于replay、梯度、超参数/检查点选择；此次没有降低KL等门禁。

只可讨论本次单seed、同族脚手架相邻变体、单波短时更新与门禁部署结果；不能声称未见family泛化、统计显著、稳健性、等算力或原八波/160题完成。Online没有产生可接受更新，因此不能成立成功学习后的CE-vs-Online能力胜负。

## 修复与错误经验

1. producer把父状态value复制到候选action Q，产生全零优势。修复可信terminal-Q派生并全零fail-closed；远端78测试通过，真实233-row loader通过。
2. PEFT alias参与parameter-name状态哈希；必须从joined receipts取得唯一behavior_version并按原alias加载，不改hash语义或放宽容差。
3. 旧CE batch1只学习1/20条transition。改为full_replay并验证manifest transitions==有效rows==batch_size；远端62测试通过。新run/replay/learner从initial开始，不继承旧checkpoint。
4. CE wave-root checkpoint解析与模型事务factory修复有回归检查；原始失败日志留存。
5. 有效PPO信号仍可触发KL拒绝。拒绝和完整回滚是结果，不能事后调门禁把它改成接受。

## 复现与保留产物

CE checkpoint留在远端`runs/formal-20x10-corrected/ce-learner/checkpoints/wave_001_step_000001_global_00000001/`；Online回滚checkpoint和optimizer/RNG在`runs/formal-20x10-corrected/wave_001/online-v2/update/checkpoint/`，before事务快照在同级`wave_ledger.artifacts/`。不下载base模型或checkpoint大文件；最终小证据包含其inventory/hashes。

已有冻结base、Lean4.28/Reap、initial adapter和join回执可重现训练。`/tmp` 在实例重建后会消失；join 的44个小文件已归档在 `build/join_v2_evidence_20261006.zip`（SHA256 `3924d9605d8b6578717eee0c9dd49f1643b3fa1d0872001dbf2e08130f27df3c`）及本地 `runs/formal-20x10/join-v2/`。恢复时先校验INDEX全部成员哈希，再按原 `/tmp/fate-m-formal-w001-join-v2/` 路径恢复，保持签名回执与manifest字节不变；工具链按bootstrap脚本和冻结pins重建。现有实例不应再次运行训练；以下是全新复现目录使用的命令形状：

```bash
E=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
JOIN=/tmp/fate-m-formal-w001-join-v2/training-inputs/manifest.json
FRESH="$E/runs/fresh-reproduction"
test ! -e "$FRESH"
mkdir -p "$FRESH/wave_001/online-v2"
cp "$E/runs/formal-20x10-corrected/wave_001/online-v2/config.json" "$FRESH/wave_001/online-v2/config.json"
sha256sum "$FRESH/wave_001/online-v2/config.json" # must equal f37680c51d0b37ac9fa8c1f906eedf3b1cfa2eff4ecfb8bd387d464898479729
python3 "$E/workstreams/orchestration/scripts/run_paired_train_wave.py" --wave-index 1 --ce-join-manifest "$JOIN" --online-join-manifest "$JOIN" --ce-config "$E/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json" --run-root "$FRESH" --model /mnt/workspace/models/REAL-Prover-fe76f68d --target-repo "$E/src/10-4-alpha-proof" --arm-order ce-first
```

Online在本次设置下预期可能被拒绝；native ROLLED_BACK应保留，不能创建假的DONE。完整审计和报告命令：

```bash
python3 "$E/scripts/audit_online_rollback.py" --experiment-root "$E"
python3 "$E/scripts/summarize_guarded_terminal_outcome.py" --experiment-root "$E"
python3 "$E/scripts/collect_corrected_evidence.py" --experiment-root "$E" --output "$E/build/corrected_final_evidence_20261006.zip"
```

上述审计/报告/归档采用不可变输出，已有产物不覆盖。CE终评的冻结root-state pins必须保留；恢复同一评测目录会复用已完成题目和attempt证据。

## 交付包

[简短交付说明](derived/results/delivery_summary_20261006.txt)与[材料来源清单](derived/results/material_sources_20261006.json)已随PR交付。整理的ZIP位于`build/modelscope_ce_online_delivery_20261006.zip`：顶层DELIVERY.txt、PR精确提交的完整code/、三个原样evidence/ZIP与逐成员MANIFEST.json；不含模型权重、私钥或缓存。FATE-M原题快照、MIT许可和课程生成/校验代码位于`data/provenance/`。

打包与验证命令（输出不可覆盖）：

```powershell
python scripts/build_delivery_bundle.py --repo build/target-repo-pr --evidence-root . --output build/modelscope_ce_online_delivery_20261006.zip
```

## 下一安全动作

实验不再训练或重跑heldout。完整证据已下载、逐成员校验并从task rows重算指标，代码与结果已交付至[目标仓库PR #1](https://github.com/wufuju2023-cell/10-4-alpha-proof/pull/1)，尚未合并。后续实验应建立新ID并冻结独立协议；若要衡量证明发现，需要不把目标作为已知假设的评测。不得将本次脚手架结果扩展为算法能力结论。checkpoint保留远端，不下载base/二进制、不删除远端资产。

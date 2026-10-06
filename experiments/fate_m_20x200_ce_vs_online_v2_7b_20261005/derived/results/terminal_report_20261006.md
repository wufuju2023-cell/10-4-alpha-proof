# ModelScope 单波对照终态报告（2026-10-06）

完整严格评测：initial **24/40（60%）**，CE **25/40（62.5%）**。Online-v2尝试更新因冻结behavior KL门禁拒绝并完整回滚；其部署策略与initial相同，**24/40来自initial身份同一性复用，未独立重跑**。双方均成功接受更新的学习效果对照没有成立。

| 策略 | 解出/40 | 前1次成功率 | 前2次成功率 | 前4次成功率 | family宏平均 | 新评测attempt数 | 评测wall分钟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| initial | 24 | 25% | 50% | 60% | 60% | 106 | 47.33 |
| CE | 25 | 27.5% | 52.5% | 62.5% | 62.5% | 103 | 46.02 |
| Online回滚部署 | 24（复用） | 25%（复用） | 50%（复用） | 60%（复用） | 60%（复用） | 0 | 0（新增） |

这里的pass@k是最多4次paired-seed顺序尝试中前k次内成功的比例，首次严格成功即停止，不是基于固定n次样本的组合估计。两个独立评测均无截断、无timeout，settings fingerprint及共同attempt的seed一致。模型生成计时initial70.199秒、CE69.283秒；Lean验证计时2747.409秒与2670.394秒，整体wall主要来自验证。Online复用一栏不产生新的token、Lean检查或GPU成本；原comparison JSON中的该行attempt/token字段是被复用的baseline证据计数。

配对结果：24题两者均成功，15题均失败，CE独有成功1题、initial独有成功0题。唯一新增是`fate_m_068_v009`（family19），证明`exact curriculum_target`。**40/40 heldout都是difficulty tier1 / direct_target且提供curriculum_target假设**。因此+1题只能描述对给定目标假设的利用，不能解释为数学证明发现能力提升；此限制比“同族泛化”更窄。

训练仅20个family的v001、同一个共同搜索波。CE覆盖20/20严格transition、137 action token、1次optimizer.step（16.831秒）。Online消费233候选、2个epoch（746.408秒），PPOloss非零，但behavior exact KL=0.0870339735 > hard limit0.05，native`ROLLED_BACK`、ledger`ABORTED`、已提交更新0。策略权重字节同一，PEFT配置仅target_modules排列不同；value head张量、optimizer/RNG/beta/scheduler均精确恢复。两臂有效暴露和成本不等。

原40–160训练题、2–8波自适应协议仍为**INCOMPLETE**；双方接受更新门禁仍为**INCOMPLETE**。没有放宽KL门禁、heldout调参或挑checkpoint。单seed、短时单波、强脚手架设置不支持统计显著、稳健性、未见family泛化、等算力或算法优劣结论。

## 证据与验证

原始ZIP：`runs/formal-20x10-corrected/final-evidence/corrected_final_evidence_20261006.zip`，SHA256 `b3e9514f179d18c1ab2beb5cf2316a7d6c029c2de369e04e52607148c3aa52d1`，1735390 bytes，1475成员逐一校验；7个checkpoint二进制仅inventory/hash，保留远端。原始raw解包与下载收据不改写。

initial report SHA256 `11675ca2cc48ab4581a5c91f243ac1e0d5d6e23ddbd89b9e1e507472465dbc0f`；CE report SHA256 `e959d9d22c9d4921b1a56744cc334d33cd5697af495f4483654f740f80eef78`。终态comparison SHA256 `9c055030987b917289616ff06472c54ed7acae134dcaaba6a3749d6ebb381bd95`。

`final_evidence_verification_20261006.json`记录ZIP和成员哈希；`final_result_audit_20261006.json`从原生task rows重算pass@k、solve/token/attempt数并验证settings/seeds、24+25成功的Lean返回码、无sorry/admit及stdout/stderr哈希。`guarded_update_audit_20261006.json`绑定native训练和回滚身份审计。`corrected_terminal_comparison/`保留远端派生报告的原字节。

PR代码检查325 passed、2 skipped；新增3项报告回归检查防止假造Online独立评测、策略不一致复用和39题伪终态。原控制器已退役，CE和报告任务原生DONE并退出；没有仍在运行的实验GPU进程。源码与配置、复现命令及远端checkpoint位置见实验README。

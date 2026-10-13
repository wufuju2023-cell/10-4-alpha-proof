"C:\xwechat_files\wxid_vskfl3t3qji122_cd47\msg\file\2026-10\fate_m_curriculum_150_adaptive_20261006.zip"

关于这个zip的说明：
════════════════════════════════════════════════════════════════════
FATE-M 150 题自适应课程数据集 — 交付说明
fate_m_curriculum_150_adaptive_20261006.zip
32.6 MB · 836 个文件 · SHA-256 F0B70FDD…5959413（完整指纹见 zip_manifest.json）
════════════════════════════════════════════════════════════════════

【这是什么】
把 150 道 FATE-M Lean 母题扩展为 144,732 条课程训练变体：
语义变换（类型类特化 / 定义展开 / 表示变换 / 实例化 / 前提干扰 / 上下文
目标移位）× 逻辑包装（合取/蕴含/析取/iff 脚手架），每条变体是一个可独立
编译的完整 Lean 源码文件，恰好一个 sorry。加上只读引用的 20×200 基线
4,000 条，合计 148,732 条。Lean 4.28 + Mathlib 4.28 全量编译验证通过。

【里面有什么】
data/additions.jsonl        课程数据本体，144,732 行（A 阶段 14,732 + B 阶段恰 130,000）
data/combined_manifest.jsonl 全量清单 148,732 行（基线 4,000 行自带完整源码，自包含）
lean/shards/                776 个公开编译 shard（每片聚合约 190 条变体）
config/                     生成协议、逐题策划覆盖、配额与饱和缺口、数据划分
scripts/                    生成/去重/验证/打包全链脚本（确定性，可复现）
build/reports/              验收报告：SUMMARY_zh.md、validation、dedup、matrix、
generation、quality_review、HANDOFF（raw 编译输出不入包）
docs/ tests/ archive/       设计文档、验证测试、历史诊断
README.md, lean-toolchain, lakefile.toml, lake-manifest.json

【怎么读】

1. 先看 build/reports/SUMMARY_zh.md —— 一页机器生成的全部关键数字。
2. 数据每行一个 JSON：id=fate_m_题号_变体号（如 fate_m_003_v201）、
   formal_statement=完整 Lean 源码、truth_basis=真值依据、
   transformation_family=变换族、split=train/validation/heldout
   （按母题族划分：120/15/15 题，同族变体永不跨划分）、
   sha256 与 semantic_fingerprint 供去重核验。
3. truth_basis 分档（未证明的推广一律不进数据）：
   mechanically_solved  115,909（80.1%）隐藏参考证明编译通过
   implied_by_source     25,179  蕴含探针通过（原题可证 ⇒ 变体可证）
   equivalent_to_source  3,520   等价探针通过（iff 双向）
   equivalent_to_source_classical 124
4. 如需编译复验：lean-toolchain/lakefile 已附，需按 lake-manifest 获取
   Mathlib 4.28 后在包根目录执行 lake env lean lean/shards/<某shard>。

【完成了什么 · 怎么验证的】

- 全量三层编译零失败：公开 776/776（sorry 警告恰 144,732=每变体一个，
  非预期警告 0）、隐藏参考证明 536/536（0 sorry）、烟雾审计 40/40。
- 验收 validate：failures=[]；273 条警告全部为饱和缺口类（结构上限的
  如实记录，非缺陷，逐条可溯 config/allocation.json）。
- 三层去重全净：144,732/144,732 唯一 SHA-256；指纹簇、规范化文本簇均 ≤2。
- 三轮质量轮：16 条策划表示变换（逐条本地 Mathlib 源码核对 + 等价探针）、
  修复干扰前提池两处生成器 bug（影响 717 条）、v2 再修复 2 个门控死条目
  （#142/#150）并为 3 个容量饥饿题（#22/#79/#133）新增语义族。
- 打包卫生：已清除旧版本残留 shard 22+14 个；包内 776 shard 与磁盘
  哈希全等，不含隐藏证明（build/hidden_proofs 按红线排除）。

【已知限制（详见包内 quality_review.md）】

- 每个语义指纹恰 2 份标签副本（协议设计），训练时每个结构出现两次。
- family-mix 份额 0.2 门限与 130,000 体量在生成器结构上不可兼得，
  已按设计以饱和缺口记录并豁免，留协议层裁决。
- 基线 4,000 条为只读引用（其权威数据在 20x200 实验，包内已含完整源码副本）。
  ════════════════════════════════════════════════════════════════════

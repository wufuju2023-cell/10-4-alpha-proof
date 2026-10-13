# Frozen data

`problems.jsonl.gz` 是本实验自包含的 20×200 只读输入，从权威课程实验机械复制并无损压缩而来。运行脚本会先校验解压后内容，再把原始 JSONL 原子地物化到 `/tmp`；Git 中不重复保存 7.6 MB 原文件：

- 来源：`experiments/fate_m_lean_curriculum_20x200_20261004/data/problems.jsonl`
- 记录数：`4000`
- SHA-256：`3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d`

正式运行前必须校验记录数和 SHA-256；切分与 wave 索引只能从冻结配置派生。不要在原文件上修改样本。

## 当前 20×10 快速协议

解压后的 `problems.jsonl` 仍是不可修改的 4000 行权威源；当前运行只消费由冻结协议机械选择的 200 行：

- `subset_20x10_train.jsonl`：20 family × `v001–v008` = 160 行；
- `subset_20x10_heldout.jsonl`：20 family × `v009–v010` = 40 行；
- `subset_20x10_manifest.jsonl`：轻量顺序、split、源行号和 record hash 清单；
- `subset_20x10_selection_receipt.json`：协议/源/输出哈希与结构断言。

这些文件只能由 `build_balanced_subset.py` 生成，不得手改：

```powershell
python build_balanced_subset.py
python build_balanced_subset.py --check
```

脚本会先复核 4000 行源文件 SHA-256、20×200 完整坐标网格、ID/字段一致性和每条 Lean statement 的 SHA-256；任何漂移都 fail closed。

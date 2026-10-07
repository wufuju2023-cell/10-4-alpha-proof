# reports/

实验产物落盘目录（E1–E8）。建议命名：

```text
reports/
  e2-window-audit.md          # 实验报告（结论 + 数据摘要）
  e2-window-audit.json        # 原始统计（机器可读）
  e1-diversity-audit.md
  e3-search-vs-sampling.md
  ...
```

规则：

1. 每份报告开头记录：日期、代码/数据来源（含 zip 名与哈希）、预算口径、随机性设置；
2. 结论区分"观测到的事实"与"解读"；
3. 派生脚本建议放仓库 `scripts/`（如 `scripts/audit_window.py`），报告里引用脚本路径；
4. 大文件（解压的 zip 内容、日志）放 `~/lmc-run`（ext4），目录里只放报告与小型统计。

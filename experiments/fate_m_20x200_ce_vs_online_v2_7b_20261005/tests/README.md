# Preflight gates

正式运行前必须依次通过：数据哈希、真实 Lean 版本/编译、REAL-Prover prompt 与终止行为、12 题端到端 smoke、A/B 单次更新与恢复、错误不计奖励、checkpoint resume、磁盘余量与结构化心跳。

任何高截断率 smoke 都视为预检失败，不能据此判断模型能力，也不能直接扩大到 4000 题。

当前快速正式范围的聚焦测试：

```powershell
python -m unittest tests/test_balanced_subset_protocol.py
```

测试会逐字节重建 20×10 工件，核验 20 family 全覆盖、每族 8 train + 2 held-out、顺序固定、split 不相交、源记录哈希，以及 4–6 小时预算、seed 与对称公平性字段没有漂移。

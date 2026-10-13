# 7 · GPU 待办清单（恢复后照单执行）

> 用途：云 GPU 重启后，从"体检"到"实验队列"逐项执行；每完成一项打勾并写回执路径。
> 状态（2026-10-08）：节点 **offline**（last seen 2d，已确认）；本清单为等待期产出，**不触发任何云端操作**。
> 相关：`6-实验总览.md` §6 待办队列；E5 导出字段清单见 `reports/htps-selection-recheck.md` §6。

## 0. 访问链路现状（恢复前先打通其一）

| 环节 | WSL（本机 /home/zhai） | my-new-linux | 说明 |
|---|---|---|---|
| Tailscale | 已安装但 **Logged out** | ✅ 在线（能看节点状态） | 网络层检查可从 my-new-linux 直接做 |
| SSH 私钥 `agent-root-mgmt` | ✅ 有 | ❌ 无 | SSH 层：要么从 WSL（先 `tailscale up`），要么把密钥放一份到 my-new-linux |
| 仓库副本（deploy 用） | 未发现 | ✅ `/mnt/gloway/projects/10-4-alpha-proof` | `deploy_cloud.sh` 需要"仓库 + 密钥"在**同一台机器** |

**最小打通方案（二选一）**：

- **方案 A（推荐）**：把 WSL 的 `~/.ssh/agent-root-mgmt` 复制到 my-new-linux 的 `~/.ssh/` → 之后可从 my-new-linux 完成全部体检与实验调度；
- **方案 B**：WSL 上 `tailscale up` 登录 + 同步一份仓库副本到 WSL → 从 WSL 执行。

> 云实例重启本身由用户在云控制台（DSW）操作；GPU 节点恢复后会重新出现在 tailnet。

## 1. 体检清单（重启后第一步，约 10–15 分钟，0 训练开销）

设 `NODE=100.91.25.4`、`KEY=~/.ssh/agent-root-mgmt`（`CLOUD_HOST`/`SSH_KEY` 可覆盖）。

- [ ] **T1 网络**（my-new-linux 可做）：`tailscale status | grep 100.91.25.4` → 不再显示 offline
- [ ] **T2 SSH**：`ssh -i $KEY -o ConnectTimeout=10 root@$NODE 'hostname; uptime; nproc'`
- [ ] **T3 GPU 硬件**：`ssh ... 'rocm-smi 2>/dev/null | head -40 || amd-smi 2>/dev/null | head -40 || nvidia-smi | head -40'`
- [ ] **T4 torch/ROCm**：`ssh ... 'python3 -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available(), torch.cuda.get_device_name(0))"'`
- [ ] **T5 关键资产**：`ssh ... 'ls /mnt/workspace/models/REAL-Prover-fe76f68d | head -5; ls -la /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt; df -h /mnt/workspace | tail -1'`
- [ ] **T6 真实冒烟**：`ssh ... 'cd /mnt/workspace/alphaproof-aligned/repo && python3 scripts/cloud_real_smoke.py'`
  → 检查 `outputs/cloud_smoke_full/report.json`：值头严格加载（missing/unexpected 为空）、LoRA applied、离线+在线各 1 次更新
- [ ] **T7 同步（如需要）**：在打通后的机器上 `bash scripts/deploy_cloud.sh`

任一项失败：先修环境，再进 §2。

## 2. GPU 实验队列（按优先级）

| # | 项 | 为什么必须 GPU | 前置（可在等待期先做） | 恢复后的动作 | 预算 |
|---|---|---|---|---|---|
| G1 | **E5 正式**：软目标 vs 硬目标 + 选择 A/B（maxk / mintok / 软） | LoRA 训练（多版对照 + 秩扫描 r∈{8,16,32,64}） | 搜索树导出补丁 + 单测（字段清单：CPU-1 报告 §6） | 跑一"带完整导出"的新波 → 训 A/B → 比较重解率 / 迁移 / 保持 | ~1 GPU-day |
| G2 | **选择规则落地**：first-verified → max-k | 新波 + CE 训练 | 选择逻辑改动 + 单测（CPU 基线已就绪：0.823→0.975） | 改 runner → 复跑 v001 波 → 训练 → 与现状对比 | 半天级 |
| G3 | **E3 搜索 vs 采样**（同预算三臂） | policy/value 推理（+ 训练臂） | 三臂 harness（mock 版先搭） | 20 题起步、同 Lean 预算 | ~1 GPU-day |
| G4 | **E1 全量多样性**（温度扫描） | 模型采样 | 降级版（历史数据）CPU 先行 | K=32/64、T∈{0.7,1.0} 采样 + 执行 | ~1 GPU-hour |
| G5 | **服务器 M3/M4**：lmc 接真模型（`/eval` 契约）；update_offline 冒烟 | GPU 服务 + 训练步 | `/eval` mock 契约测试 | 真模型小波次 → 轨迹导出 → update_offline | 半天级 |
| G6 | **校准项**：value 权重 1e-3 标定曲线（status.md 遗留） | 训练 | — | 小规模训练曲线 | 小时级 |
| G7 | E4 / E6 / E7 / E8 | 训练 / 采样 | 按各自判据 | 见 `3-实验计划.md` | 各 0.5–1 GPU-day |

**建议默认顺序**：T1–T6 → G1 → G2 → G3 → G5 → G4 → G6 → G7。

## 3. 等待期已完成（CPU 侧，2026-10-08）✅

- ✅ G1 前置：搜索树完整导出 `alphaproof/data/tree_export.py` + 测试（`tests/test_tree_export.py`，4 测试）；
- ✅ G2 前置：选择规则旋钮 `SearchConfig.optimal_child_policy`（first/most_visited/shortest_action）+ 测试（5 测试）；
- ✅ G3 前置：三臂 harness `scripts/e3_mock_three_arms.py`（mock 版已跑通；报告 `reports/e3-mock-harness.md`）；
- ✅ G5 前置：`lmc/eval_client.py` + 契约 mock 测试（3 测试通过）；
- ➕ 顺手：**E1 降级多样性审计**完成（`reports/e1-diversity-audit.md`）。

## 4. 恢复后的默认动作

1. 按 §1 逐项体检 → 结果写回本文件（打勾 + 回执路径）；
2. 向用户报告体检结论 + 确认先跑哪个队列项；
3. 从 §2 最高优先级开始执行。

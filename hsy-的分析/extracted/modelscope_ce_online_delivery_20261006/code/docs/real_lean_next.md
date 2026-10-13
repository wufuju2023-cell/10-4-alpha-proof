# 真实 Lean 环境接入（下一步）

## 1. 环境位置与版本

| 项 | 值 |
| --- | --- |
| 安装根目录 | `/mnt/gloway/projects/lean-4.28-reap`（exfat 上的**挂载点**） |
| 实际数据 | `/home/a/lean-4.28-reap`（ext4），bind mount 到上者；已写入 `/etc/fstab` 持久化 |
| elan | `/mnt/gloway/projects/lean-4.28-reap/elan`（`ELAN_HOME`；随 bind mount 落在 ext4） |
| 安装脚本 | `/home/a/lean-4.28-reap/install_lean428.sh`（幂等，可重跑） |
| Lean | `leanprover/lean4:v4.28.0-rc1` |
| Reap | `IQuestLab/reap@0090d73c5f739e4d74000e053b00fd0148ff46aa` |
| v1 训练补丁 | `0001-training-endpoints-and-value` / `0002-training-observer` / `0003-strict-value-errors` |
| mathlib | `leanprover-community/mathlib4` rev `v4.28.0-rc1` |
| 状态标记 | `state/01_elan.done … 99_install_ok.done`；日志 `logs/install.log` |

> ⚠️ 为什么不用原生路径：`/mnt/gloway` 是 **exfat**（不支持符号链接），
> elan/lake 全流程依赖 symlink，直接安装会失败（`Operation not permitted`）。
> 因此采用 ext4 bind mount：路径语义保持在 Gloway 下，底层文件系统支持 symlink。

## 2. 应用本仓库的对齐补丁

在基础环境编译通过后：

```bash
export ELAN_HOME=/mnt/gloway/projects/lean-4.28-reap/elan
export PATH="$ELAN_HOME/bin:$PATH"
cd /mnt/gloway/projects/lean-4.28-reap/reap
for p in /mnt/gloway/projects/10-4-alpha-proof/lean/patches/000*.patch; do
  patch -p1 --dry-run -i "$p" && patch -p1 -i "$p"
done
cd ../runtime && lake build
```

补丁内容：
1. `0001-align-options.patch`：`reap.prior_temperature` 默认 50 → **200**；新增 `reap.c_and=64`、`reap.unvisited_penalty=32`、`reap.no_legal_actions_value=-40`；
2. `0002-align-puct.patch`：`SearchHyperparameters` 增 `cAnd/unvisitedPenalty`；`computePUCTScores` 未访问子用 `V(s)-c_pen`，AND 节点探索项乘 `c_AND`；
3. `0003-align-fallback.patch`：值服务失败兜底 `-1000 → -40`。

## 3. 用真实内核跑单道佩尔题（里程碑）

已有素材：
- 题面：`assets/pell/target.lean`（`CodexMathFive.Pell.Target`）；
- 旧 runtime：`/mnt/gloway/projects/reap-new-update-model/v1-result/20260828-real7b-pell-success/code/`
  - `containers/cpu/runtime/ReapRuntime.lean`、`Smoke.lean`（Lean 侧入口）；
  - `cpu_runtime/`（Python 编排、`mock_services.py` 可无 GPU 提供 policy/value 假服务）。

推荐路径（不依赖 GPU）：
1. 用 runtime 的 `ReapRuntime` 接口在 Pell Target 上运行 `reapMCTS`，policy/value 走 `mock_services.py`；
2. 校验 observer JSONL（Verdict / RolloutSink）与最终 `checkProof`；
3. 与 `alphaproof/mcts` 的 Python Mock 搜索结果对照（节点数/脚本/价值目标）。

之后（需要 GPU）：
- 把 CPU 侧 `mock_services` 换成云端 `scripts/cloud_real_smoke.py` 暴露的 policy/value 服务；
- 收集轨迹 → `update_offline` / `update_online` 各跑一次更新。

## 4. 已知约束

- Gloway 为 exfat：不支持符号链接，`python3 -m venv` 请建在 home（如 `~/venvs/alpha104`）；
- 云端容器无 Lean 工具链（已确认），真实内核搜索在 my-new-linux 侧运行；
- Mathlib 缓存首次下载约数 GB，已按断点续传设计（`lake exe cache get` 可重跑）。

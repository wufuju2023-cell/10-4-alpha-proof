# 云实例存储审计（2026-10-09）

- 实例：`dsw-2239033-79dcbd66ff-nkh7x`（ROCm 7.2 / gfx942，23 核）；持久目录 `/mnt/workspace`
  （`/home/admin/workspace` 与它同 inode——同一份，不重复占额）。
- 用量：**≈92 GB / 100 GB 配额**（控制台 91.75 G）。
- 去重背景：2026-09-28 跑过 `_archive/dedupe.sh`，把全盘文件在 `_archive/blobs/` 建**硬链接**镜像
  （logical 88.7 GB / unique 76.1 GB；10522 文件 / 7575 唯一内容）。
  **清理协议：删目录后必须清孤儿 blobs**（`find /mnt/workspace/_archive/blobs -type f -links 1 -delete`），
  否则空间不释放（blobs 是这些内容的最后一份链接）。
- 逻辑大小来自 `_archive/MANIFEST-all.tsv` 聚合（与 `du` 单遍口径一致）。

## 建议分档

### Tier A · 可立即删（≈29.5 GB；可再生成或已在公网）

| 项 | 大小 | 依据 |
|---|---:|---|
| `models/REAL-Prover-fe76f68d` | 14.2 GB | 公网 HF `FrenzyMath/REAL-Prover@fe76f68d9a88f342cb7b546307c20292fea9cced`；锁文件已存 `assets/model-lock/` |
| `full_modelscope/model_cache` | 3.2 GB | 下载缓存 |
| `full_modelscope/inference_v1/merged_eval11` | 3.2 GB | base+adapter 可再合并；adapter 备份 `backups/full_best_adapter_eval11.tar.gz`（66 MB，先上传 HF） |
| `experiments/.../runtime/toolchains` | 2.6 GB | Lean 工具链可重建（`docs/real_lean_next.md`） |
| `full_modelscope/runs` 中间检查点（32 个留 final+best 2 个） | ≈3.5 GB | 中间 eval 检查点 |
| `experiments/...` 状态体（`trainer_state.pt`×3、wave artifacts、smoke runs） | ≈2.5 GB | 只留 adapter + `value_head.pt` + 回执 |
| `.configs/code-server` | 0.3 GB | 扩展缓存 |

### Tier B · 建议删（≈56.9 GB；老实验线快照，删前先留小件/抽查）

| 项 | 大小 | 留存动作 |
|---|---:|---|
| `new_value_head/pell-fullv3-resume9-v1/snapshots` | ≈22.8 GB | 保留 `success/`、`service*/`（<2 MB） |
| `new_value_head/pell-fullv3-resume8-v1/snapshots` | ≈7.0 GB | 同上 |
| `new_value_head/categorical-snapshots-fullv3` | 9.5 GB | HF 已有同类 `backend.json`；建议抽查 1 个快照后删 |
| `new_value_head/categorical-snapshots-79efd240` | 3.9 GB | 旧代 |
| `reap-runs/ms-epoch2-20260828`（大部） | ≈13.7 GB | 先上传 `SFT-mechanism-r2-evidence.tar`（868 MB）与小 manifests |

### Tier C · 保留并补齐备份

- 值头：`new_value_head/heads-79efd240/*`（两套 `value-head.pt` 各 3.7 MB + report）；`artifact-fullv3/`（已全在 HF，可只留本地小份）。
- FATE 实验核心（≈0.9 GB）：`assets/initial_lora_r16_a32_seed20261004`（161 MB）、两套 CE learner 的 `adapter + value_head.pt`、online-v2 的 `adapter + value_head.pt`、`data/`（8.8 MB）、`build/*.zip`（14 个交付/工具包 ≈7 MB）。
- 数据集：`new_value_head/dataset*`（270 MB）、`head-runs/datasets/fullv3-train-features`（572 MB）。
- **私密项（勿公开）**：`backups/container-config.tgz`（tailscale state + SSH keys）、`backups/hf-token`、`restore-container.sh`。

## 备份缺口（上传清单）

1. **HF `alpha-proof-open-source/alphaproof-full-v3-value-head`**：补 `value-head.pt`、`value-head-initial.pt`、`train80k-p64-r64/*`；
2. **HF `WufuJu/modelscope-cloud-archive`**（现为空仓库）：传 FATE adapters（4×161 MB）、`full_best_adapter_eval11.tar.gz`（66 MB）、`SFT-mechanism-r2-evidence.tar`（868 MB）、各 `value_head.pt` 与回执；
3. **HF datasets**（新建）：值头 train/val/test jsonl（270 MB）、`fullv3-train-features`（572 MB）、FATE `data/`（8.8 MB）；
4. **GitHub**：本地未推送内容（`docs/architecture`、`tasks/`、diversity-mcts 5/6/7、`reports/`、notebooks、scripts、`lean/patches/0004–0005`、`assets/model-lock/`、`hsy-分析` 交付与证据 zip…）→ commit & push（`~/.ssh/github-104` 已验证可用）。

## 执行顺序（建议）

1. 先做 §备份缺口 的 1–3 与 GitHub 推送（纯备份，不删）；
2. 抽查 Tier B 的 1–2 个快照对应 HF 已有内容；
3. 删 Tier A → 清孤儿 blobs → 复核用量；
4. 删 Tier B → 清孤儿 blobs → 目标：从 ≈92 GB 降到 **≤15 GB**。

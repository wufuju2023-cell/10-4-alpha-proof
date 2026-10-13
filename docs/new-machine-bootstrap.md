# 新机器 · 主线环境清单（lmc-core + gpu-serve，2026-10-09）

> 目标：在全新 DSW 实例/新机器上，按本清单恢复到"可跑 `cloud_real_smoke` + lmc-core 波次"的状态。

## 1. 代码（GitHub，公开）

- `git clone https://github.com/wufuju2023-cell/10-4-alpha-proof.git`（只读；写用 SSH key `~/.ssh/github-104`）。
- 关键内容：`alphaproof/`（对齐内核）、`scripts/`（deploy/smoke/e2/htps/e1/e3…）、`tests/`、
  `docs/`（`new-python-version-mcts-plan/` 规格 02–08、`diversity-mcts/`、`architecture/`）、
  `lean/patches/0001–0005`、`tasks/`。

## 2. 模型与权重（HuggingFace，公开）

| 资产 | 来源 | 说明 |
|---|---|---|
| 基座模型 | `FrenzyMath/REAL-Prover@fe76f68d9a88f342cb7b546307c20292fea9cced` | Qwen2.5-Math-7B 微调；锁文件在 `assets/model-lock/` |
| 值头 artifact | `WufuJu/v1-1-fullv3-artifact`（backend.raw.pt / backend.full.pt）+ `alpha-proof-open-source/alphaproof-full-v3-value-head`（backend.json 等） | 对应云端 `new_value_head/artifact-fullv3` |
| 值头训练产物 | `alpha-proof-open-source/alphaproof-full-v3-value-head`（待补 `value-head.pt`） | 3.7 MB，云端 `heads-79efd240` |
| FATE adapters / 数据集 | `WufuJu/modelscope-cloud-archive` + datasets 仓库（待上传） | initial LoRA、CE/online adapters、train jsonl |

拉取示例：

```bash
HF_TOKEN=... hf download FrenzyMath/REAL-Prover --revision fe76f68d9a88f342cb7b546307c20292fea9cced --local-dir /mnt/workspace/models/REAL-Prover-fe76f68d
HF_TOKEN=... hf download WufuJu/v1-1-fullv3-artifact --local-dir /mnt/workspace/new_value_head/artifact-fullv3
```

## 3. Lean / Reap 环境（本机重建）

- 按 `docs/real_lean_next.md` + `lean/README.md`：elan + Lean `4.28.0-rc1` + mathlib `v4.28.0-rc1`
  + **patches 0001–0005**（0004/0005 = canonical provenance / selected-path，已并入本仓 `lean/patches/`）。
- 参考脚本：`/mnt/gloway/projects/lean-4.28-reap/{install_lean428.sh,elan-init.sh}`（ext4 上建 `ELAN_HOME`；gloway 不支持 symlink）。
- 备选（省时）：从旧实例带 `runtime/toolchains/lean-4.28.0-linux`（2.6 GB）。

## 4. Python / 容器

- DSW ROCm 镜像（torch 2.12 + rocm7.2 已验证）；`pip install -r requirements.txt`
  （核心测试仅 pytest；真实模型需 transformers/peft，云端镜像一般自带 torch）。
- venv 经验：`~/venvs/alpha104`。

## 5. 凭据与恢复

- HF token（用户 `WufuJu`，org `alpha-proof-open-source`）；
- SSH：私钥 `agent-root-mgmt`（WSL），公钥已固定在旧实例 `authorized_keys`；
- 容器恢复：`backups/restore-container.sh`（backup / restore / rootkey 三模式，含 tailscale 恢复流程）；
- **私密备份（勿入公开仓库）**：`container-config.tgz`（tailscale state + SSH keys）与 `hf-token`。

## 6. 验收

1. `python3 scripts/cloud_real_smoke.py --model <base> --head <value-head.pt>` →
   `report.json`（值头严格加载、LoRA applied、离线+在线各 1 次更新）；
2. lmc-core：`lmc smoke` / `lmc mcts-smoke`（CPU），随后 `lmc wave → lmc export`（store/CLI 落地后）。

# gpu-serve · GPU 侧参数化 CLI

> 主线固定：`lmc-core`（CPU）+ `gpu-serve`（GPU）。本文件是 `gpu_serve/` 包的使用说明。
> 边界（见 `docs/architecture/core-and-branches.md` §3/§4）：本包**只做装载与 CLI 层**，
> base / LoRA / value head 全部参数注入，超参全可调；
> **CE 更新逻辑（`update_offline` / `update_online`）冻结，只允许调用、不允许改算法**。

## 1. 安装与入口

仓库根目录新增 `pyproject.toml`（已含 console script）：

```bash
# 在有 torch/transformers/peft 的 venv 里（云端 python3 已装好）
cd /mnt/workspace/alphaproof-aligned/repo
python3 -m pip install -e . --no-deps        # 或 pipx / 直接 PYTHONPATH=.

gpu-serve --help                            # console script
python -m gpu_serve --help                  # 等价的模块入口
PYTHONPATH=. python -m gpu_serve --help     # 不安装时的兜底
```

无 torch 的环境（如 `~/venvs/alpha104`）也能跑：`--help`、`versions`、
`serve-eval --engine mock`；其余命令按需报错提示缺依赖。

## 2. 命令总览

| 命令 | 作用 | 依赖 |
|---|---|---|
| `gpu-serve serve-eval` | 启动 `/eval` 契约 HTTP 服务（policy 候选 + value） | torch + transformers（mock/tiny 除外） |
| `gpu-serve train offline` | 调 `OfflineLearner` 做专家迭代（CE） | torch + transformers/peft |
| `gpu-serve train online` | 调 `OnlineLearner`（参数化壳 + 事件回放） | 同上 |
| `gpu-serve eval heldout` | 留出池评测：policy CE / value CE / d̂ 误差 | 同上 |
| `gpu-serve pool pull\|push\|merge-logprobs\|stats` | GPU 训练池：拉取/校验、public 上传、logprob 回填、统计（§6） | 无 torch 依赖；网络走 huggingface_hub 或 hf CLI |
| `gpu-serve versions` | 工具链 / base / head / LoRA 版本清单 | 无（读 JSON 不需要 torch） |
| `gpu-serve smoke` | `--tiny` 全回路；真实 7B 装载 + 前向 | torch（真实模式加 HF） |

`pool` 子命令已实现（2026-10-09，§6），命令面冻结于 `docs/architecture/core-and-branches.md` §1。

所有子命令都支持 `--config <TOML>`（可放在子命令前或后），优先级：
**CLI 显式 flag > TOML > 代码默认值**。

## 3. serve-eval（`/eval` 契约）

契约（`docs/new-python-version-mcts-plan/02-architecture.md` §5；客户端参考
`lean-mcts-cpu-server/lmc/eval_client.py`）：

```text
POST /eval
  {"prompt": "<tactic state>", "k": 6, "temperature": 1.0,
   "policy_version": "v123", "request_id": "..."}
→ 200 {"candidates": [{"tactic": "...", "logprob": -3.21}, ...×k],
       "value": {"v": -7.4, "d_hat": 7.4},
       "model_revision": "REAL-Prover-fe76f68d", "adapter": "..."}
```

- `logprob` = 模型原始策略下该候选序列的 **sum log p**（temperature 只影响采样）；
- `v = -d_hat`，`d_hat` 为 64 桶头期望剩余步数；
- 认证：`Authorization: Bearer <token>`，token 从 `--token-file` 读（600 权限，只驻内存、不进日志）；
  不配置 token 则不做鉴权（建议仅本机/SSH 隧道场景）；`GET /health` 不鉴权。

```bash
gpu-serve serve-eval \
  --base /mnt/workspace/models/REAL-Prover-fe76f68d \
  --lora none \
  --head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
  --dtype bf16 --host 0.0.0.0 --port 18080 --token-file ~/.config/lmc/token

# 无依赖联调（契约/鉴权/端口）
gpu-serve serve-eval --engine mock --port 18080
# CPU 小模型
gpu-serve serve-eval --tiny --engine tiny
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--base` | 无 | HF 路径或 id；也可直接指向 PEFT adapter 目录（自动读 `base_model_name_or_path`） |
| `--lora` | `none` | PEFT adapter 目录；或裸 state_dict（配合 `--rank/--lora-alpha/--lora-dropout/--lora-target-modules`） |
| `--head` | 无 | 64 桶 value head 权重；键名兼容裸 / `{'value_head': ...}` 包装 / 无 `net.` 前缀 |
| `--dtype` | `auto` | auto = GPU bf16 / CPU fp32；可选 bf16/fp16/fp32 |
| `--device` | `auto` | auto/cuda/cuda:N/cpu |
| `--hidden-size` | 0 | value head 输入维度；0 = 从 base config 推断（Qwen3.8-27B 等换模型时必须核对） |
| `--value-mid/--value-bins` | 256/64 | 头形状；bins=64 为冻结口径 |
| `--engine` | `auto` | auto/hf/tiny/mock |
| `--host/--port` | 127.0.0.1/18080 | 绑定地址与端口 |
| `--token-file` | 无 | Bearer token 文件（不配置则不鉴权） |
| `--max-new-tokens` | 64 | 候选 tactic 生成上限 |
| `--stop-at-newline` | 开 | 候选截断到首个换行 |
| `--model-revision/--adapter` | 自动 | 响应中透传的版本标识（默认 base / LoRA 目录名） |
| `--allow-download` | 关 | 默认 `local_files_only=True`（可复现）；需要联网拉 HF 时显式打开 |

## 4. train offline / train online

```bash
# 离线专家迭代：pool → CE 更新（batch=wave 口径，超参全部可调）
gpu-serve train offline \
  --base /mnt/workspace/models/REAL-Prover-fe76f68d --lora none \
  --head .../value-head.pt \
  --pool /mnt/workspace/pool/wave-001 \
  --steps 1 --batch-size 64 --micro-batch-size 16 \
  --lr 1e-4 --value-lr 3e-4 --value-coef 1e-3 --sft-mix 0.1 \
  --out run/wave-001-receipts.jsonl --save-dir run/wave-001-ckpt

# 在线更新壳（事件回放；事件需带 logprob_old/reward，正奖励须 terminal_verified）
gpu-serve train online --base ... --head ... --events run/events.jsonl \
  --batch-size 1 --beta-kl 0.05 --lr 1e-4
```

| 参数 | 默认 | 映射（`alphaproof.config.TrainConfig`） |
|---|---|---|
| `--steps` | 1 | offline 的 optimizer step 数 |
| `--batch-size` | 64 | offline：每 step 样本数；online：`learn_batch_size` |
| `--micro-batch-size` | 16 | `micro_batch_size`（梯度累积切块） |
| `--lr` | 1e-4 | `policy_lr` |
| `--value-lr` | 3e-4 | `value_lr` |
| `--value-coef` | 1e-3 | `value_coef`（官方 Table 6） |
| `--beta-kl` | 0.05 | `beta_kl`（在线 KL 锚） |
| `--sft-mix` | 0.1 | `sft_mix`（需配 `--sft-pool`） |
| `--disproof-weight` | 1.0 | `disproof_weight` |
| `--include-disproof/--no-include-disproof` | 开 | `include_disproof` |
| `--include-timeout/--no-include-timeout` | 关 | `include_timeout`（官方：超时不进训练） |
| `--max-grad-norm` | 1.0 | `max_grad_norm` |
| `--seed` | 0 | 回放采样种子 |
| `--rank/--lora-alpha/--lora-dropout/--lora-target-modules` | 16/32/0.02/7 个投影 | 仅构造**裸** LoRA state_dict 时使用；目录式 adapter 自动读配置 |

`--pool`/`--sft-pool` 接受目录（`shard-*.jsonl` 优先，否则 `*.jsonl`）或单个 jsonl，
行格式即冻结的 Transition schema（`docs/new-python-version-mcts-plan/05` §2）。
`--save-dir` 原子保存 `value-head.pt` +（PEFT 时）`adapter/` + `meta.json`。

LoRA 装载模式：`train *` 以**可训练**模式挂 adapter（保证 policy 优化器参数组非空）；
`serve-eval`/`eval heldout` 以推理模式挂载（冻结、省显存）。
CPU-only 机器若遇到本地 CUDA 构建的 torch 探测设备卡住，显式加 `--device cpu`
（tiny 测试即走该路径）。

## 5. eval heldout / versions / smoke

```bash
gpu-serve eval heldout --base ... --head ... --pool run/heldout --micro-batch-size 8 --out heldout.json
gpu-serve eval heldout --base ... --policy-only --pool run/heldout          # 只评 policy

gpu-serve versions --base ... --head ... --lora ...        # 版本/形状/大小清单（不加载权重）

gpu-serve smoke --tiny --out smoke.json                    # 全回路（前向+offline+online）
gpu-serve smoke --base ... --head ... --skip-update        # 真实装载 + 1 次前向
gpu-serve smoke --base ... --head ... --pool run/pool --out smoke.json   # 再加 1 步 offline
```

`eval heldout` 指标：`policy.ce_mean/perplexity/ce_by_kind`、`value.ce_mean/d_hat_mae/bin_acc_lt1`
（d̂ 与 d=-value_target 的比较；数值口径与训练一致——调用 `OfflineLearner._policy_ce` 与
`two_hot_target`，不复制算法）。

## 6. pool（GPU 训练池）

> 状态：2026-10-09 实现（`gpu_serve/pool.py`；`tests/test_gpu_serve_pool.py` 24 用例
> 双 venv 通过；HF public 匿名 pull + 真实 shard merge 演示通过）。命令面冻结见
> `docs/architecture/core-and-branches.md` §1；shard + manifest 规格见
> `docs/new-python-version-mcts-plan/05` §1/§3。同步方向固定 **CPU export → GPU pool**，
> GPU 侧只做 logprob 回填（在线支线用）。

```bash
# 1) 拉取：HF public（匿名可读）或本地导出目录
gpu-serve pool pull \
  --source alpha-proof-open-source/10-4-alpha-proof-trajectories \
  --manifest wave_001_fate_m_v001/manifest.json \
  --dest /mnt/workspace/pool/wave_001_fate_m_v001
gpu-serve pool pull --source ~/lmc-store/export/wave_001_fate_m_v001 \
  --manifest manifest.json --dest /mnt/workspace/pool/wave_001_fate_m_v001

# 2) 上传（public；token 只从 HF_TOKEN 读，不落盘不回显）
HF_TOKEN="$(/home/zhai/.local/bin/token_get.sh hf-write-token-for-alpha-proof-org)" \
  gpu-serve pool push --src /mnt/workspace/pool/wave_001_fate_m_v001 \
  --repo alpha-proof-open-source/10-4-alpha-proof-trajectories
gpu-serve pool push --src ... --repo ... --dry-run   # 只本地校验 + 打印计划，不触网

# 3) logprob 回填（GPU 采样侧产出 jsonl → 池内 logprob_old）
gpu-serve pool merge-logprobs --pool /mnt/workspace/pool/wave_001 \
  --logprobs run/gpu_logprobs.jsonl [--out run/merged.jsonl] [--strict]

# 4) 统计（默认人读表格；--json 机读）
gpu-serve pool stats --pool /mnt/workspace/pool/wave_001_fate_m_v001 [--json]
```

| 参数 | 说明 |
|---|---|
| `pull --source` | HF repo id（`org/name`，匿名可读）或本地导出目录 |
| `pull --manifest` | HF 源 = 仓库内相对路径（如 `wave_<id>/manifest.json`）；本地源 = 文件路径（相对 source 优先解析） |
| `pull --dest` | 目标池目录；shard 按 manifest 相对名落盘，`manifest.json` 一并回写（池目录自包含） |
| `pull --force` | 行已存在且 sha256 一致时默认跳过（幂等 / 断点续传）；`--force` 强制重拉 |
| `push --src` | 待上传目录（须含 `manifest.json`；上传前逐 shard 校验） |
| `push --dry-run` | 不触网：打印 `[plan]` 行与 JSON 摘要 |
| `push --path-prefix` | 仓库内路径前缀（默认根目录） |
| `merge-logprobs --out` | 默认 `<logprobs>.merged.jsonl` |
| `merge-logprobs --strict` | 有池行未 join 时退出码非零（默认仅报告） |
| `stats --json` | 机读统计（默认人读表格） |

**校验口径**（pull / push 共用）：逐 shard 校验 `sha256`、行数（manifest `count`，
兼容发布侧 `lines`）与 `bytes`；再对 `counts.transitions` / `counts.shards` 做合计校验。
任何不一致 → 退出码 1，不静默放行。

**merge-logprobs join 语义**（独立实现，对齐 `lmc/store.py`，不 import lmc）：

- 键 = `(search_id, state_hash, action)`；
- state hash 兼容 `state_hash` / `state_before_sha256`（`extra.*` 与顶层都认，
  另认 `extra.selected_path_step.state_before_sha256`）；
- search_id 解析 `search_id` → `extra.search_id` → `ref.search_id` → `extra.ref.search_id`；
  `extra.attempt_id` 作为另存回退键（FATE/CE 证据批次无 search_id 时用）；两侧都缺
  search_id 时按 `(None, state_hash, action)` join，报告 `joined_without_search_id`；
- logprob 取值 `logprob_old`，缺失回退 `logprob`；重复键后值覆盖（报告 duplicate 数）；
- 输出 = 池全量行 + 回填后的 `logprob_old`（单 jsonl），可直接作
  `train online --events` / `pool stats --pool` 输入。

对接关系：CPU `lmc export`（`05` §3）产出 shard+manifest → `pool pull` 入 GPU 池 →
`train offline`；在线支线由 GPU 采样产 logprob → `merge-logprobs` → `train online`。

## 7. TOML 配置（可选）

```toml
[model]
base = "/mnt/workspace/models/REAL-Prover-fe76f68d"
lora = "none"
head = "/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt"
dtype = "bf16"
device = "auto"
hidden_size = 0
value_mid = 256
value_bins = 64

[lora]            # 仅裸 state_dict 时用
rank = 16
alpha = 32
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

[train]
steps = 1
batch_size = 64
micro_batch_size = 16
lr = 1e-4
value_lr = 3e-4
value_coef = 1e-3
beta_kl = 0.05
sft_mix = 0.1

[serve]
host = "0.0.0.0"
port = 18080
token_file = ""
max_new_tokens = 64
stop_at_newline = true
```

## 8. 换 base 模型 checklist（如 Qwen3.8-27B）

1. **hidden size**：`--hidden-size` 留 0 时自动读 base `config.json`（含 `text_config` 回退）。
   值头形状 = `hidden_size → 256 → 64`；不匹配时 `--strict-head` 可直接报错。
2. **tokenizer**：`--tokenizer` 可单独指向（base 与 tokenizer 不同源时）；缺 pad token 自动退化为 eos。
3. **LoRA target modules**：新架构的注意力/MLP 投影名不同（如 Qwen 的 `q_proj/k_proj/v_proj/o_proj/
   gate_proj/up_proj/down_proj` 也在，但 MoE 层名不同）。目录式 adapter 自动读；
   裸 state_dict 用 `--lora-target-modules`。
4. **价值头重训**：换 base 后旧头语义失效——用新 base 的 hidden 重训 64 桶头，
   产出新的 `value-head.pt` 再挂 `--head`。
5. **CPU 端联动**：`lmc` 控制器的 `[gpu] eval_url` 指向本服务；`policy_version` 在 job/wave 落盘
   （版本三元组强校验，`docs/new-python-version-mcts-plan/02` §6）。

切换示例：

```bash
gpu-serve serve-eval --base /mnt/workspace/models/Qwen3.8-27B \
  --lora /mnt/workspace/adapters/qwen-lora-r32 \
  --head /mnt/workspace/new_value_head/qwen-head/value-head.pt \
  --hidden-size 0 --dtype bf16 --model-revision Qwen3.8-27B
```

## 9. 测试与云端验证

```bash
# CPU（无 torch 的基线 venv）：mock/help/versions 全跑，torch 用例自动跳过
cd /mnt/gloway/projects/10-4-alpha-proof
PYTHONPATH=. ~/venvs/alpha104/bin/python -m pytest tests/ -q

# 有 torch 的 venv：tiny 全回路（smoke/train/heldout/serve-eval tiny）也跑
PYTHONPATH=. ~/venvs/torch/bin/python -m pytest tests/test_gpu_serve_cli.py -q

# pool 子命令（纯 stdlib / mock 文件，不联网；任意 venv 可跑）
PYTHONPATH=. ~/lmc-run/venvs/torchcpu/bin/python -m pytest tests/test_gpu_serve_pool.py -q

# 云端（双跳同步后）
ssh root@100.91.25.4 'cd /mnt/workspace/alphaproof-aligned/repo && \
  python3 -m gpu_serve smoke --base /mnt/workspace/models/REAL-Prover-fe76f68d \
  --head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
  --out /mnt/workspace/alphaproof-aligned/repo/outputs/gpu_serve_smoke/report.json'
```

## 10. TODO（明确留给后续）

- `train online` 的 **live 事件流**：常驻服务 + 行为策略版本 / 采样端 logprob 通道属主线，当前仅
  `--events` 回放壳（报告里带 `"mode": "replay-shell"` 与 `"todo"` 字段）；
- `/eval` 连续批处理（`EvalBatcher`，窗口 5–20ms / 批 ≤64）未做，v0 单请求即到即算；
- 真实模式的 `train online` 冒烟依赖已落盘事件（需 GPU 侧采样端产 `logprob_old`）。

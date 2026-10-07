# 05 · 轨迹仓库与训练导出

## 1. 目录布局

```text
$LMC_STORE/
  waves/<wave_id>/<problem_id>/<search_id>/
      job.json            # 题目 / 预算 / 版本三元组 / policy_version / config_hash
      steps.jsonl         # 事件流（04 §6）
      tree.json           # 轻量树快照（节点 / 边 / 统计 / is_optimal）
      transitions.jsonl   # solved 时：extract_transitions 输出（未含 logprob_old）
      result.json         # solved / status / best_script / 计数 / 耗时
      strict_receipt.json # 独立严格复验收据
  export/<wave_id>/
      shard-0001.jsonl
      manifest.json
```

- 写入：临时文件 + `rename` 原子替换；`steps.jsonl` 为 append-only。
- 失败样本（exhausted / timeout）同样落盘：训练默认剔除，但审计与调试需要。

## 2. Schema（对齐仓库既有）

**Transition（`alphaproof/data/events.py`，唯一训练口径）**

```json
{"prompt": "<tactic state>", "action": "<tactic>",
 "value_target": -7.0,             // -d(s)；终端 0
 "kind": "proof",                  // proof | disproof | timeout
 "solved": true, "terminal_verified": true,
 "logprob_old": null,              // 在线支线用；GPU 侧 merge
 "reward": null,                   // 与 value_target 分离
 "extra": {"search_id": "...", "state_hash": "...", "policy_version": "vN",
           "lean": "4.28.0-rc1", "mathlib": "v4.28.0-rc1", "reap": "0090d73c"}}
```

**值语义（与 `examples/update_modes_walkthrough` 数值一致）**

```text
v(s) = -d(s)（d = 剩余步数）；
OR: v = -1 + v(最优子)；AND: v = min(子)；终端 0。
```

## 3. 导出规则（`lmc export`）

- 只导出 `is_optimal` 子树（`extract_transitions` 语义）；focus 伪动作不落样本；
- 去重：同一 `(problem_id, state_hash, action)` 默认保留 `policy_version` 最新（可 `--keep-all`）；
- 分片：`--shard-size 4096`；`manifest.json` 记录计数 + sha256 + 版本三元组 + policy_version；
- 在线支线：`--merge-logprobs <gpu_logprobs.jsonl>`（按 `(search_id, state_hash, action)` join；tokenizer 在 GPU 侧，本机不产 logprob）。

## 4. 严格复验收据（strict_receipt）

- 触发：`solved` 后自动；或 `lmc verify --strict --search <id>`；
- 方法：把 `best_script` 生成独立 `.lean` 文件（禁止 sorry/admit；含课程头 / import），一次性 `lake env lean` 全文件编译（内核检查）；
- 字段：

```json
{"state": "PASS", "proof_sha256": "...", "lean_version": "4.28.0-rc1",
 "mathlib_rev": "v4.28.0-rc1", "reap_commit": "0090d73c", "patchset_hash": "...",
 "exit_code": 0, "stdout_sha256": "...", "stderr_sha256": "...", "elapsed_s": 12.3,
 "log": "strict.log"}
```

- 失败：`result.json` 的 solved 撤回为 `unverified`（不允许未复验样本进 CE 主线）。

## 5. 与 update_offline / update_online 的对接

- **update_offline（主线）**：`OfflineBatchBuilder.build(replay, batch_size)` 消费 Transition（字段同名直接读）；SFT 混比与 timeout 剔除沿用 `TrainConfig` 默认（`sft_mix=0.1`、`include_timeout=False`）；
- **update_online（支线）**：需要 `logprob_old` 与行为策略版本；由 GPU 采样侧产出、`--merge-logprobs` 合入；正奖励仍须 `terminal_verified=true`；
- 验收冒烟：一波 shard → `run_expert_iteration`（离线）1 步更新成功；一份 merge 后 shard → `update_online` learner 1 步成功。

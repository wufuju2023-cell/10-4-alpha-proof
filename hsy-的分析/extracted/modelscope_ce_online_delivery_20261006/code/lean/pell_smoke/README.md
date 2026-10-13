# lean/pell_smoke · 真实 Lean 内核 × 佩尔题

目标：在真实 Lean 4.28 内核里用对齐后的 MCTS 跑**一道佩尔题**，产出
`progress.jsonl` / `result.json` / `raw_tree.json` / `wall_clock.jsonl` / observer 事件。

## 1. 前置

- 环境：`/mnt/gloway/projects/lean-4.28-reap/runtime` 已 `lake build` 成功
  （安装 + 对齐补丁见 `docs/real_lean_next.md`）；
- 驱动：复用 v1-result 的 `cpu_runtime`（`batch_solver.py`），其调用形式为
  `lake env lean <theorem_file>`（cwd = `--project-dir`）。

## 2. Mock 流程（无 GPU，验证链路）

```bash
export ELAN_HOME=/mnt/gloway/projects/lean-4.28-reap/elan
export PATH="$ELAN_HOME/bin:$PATH"
V1=/mnt/gloway/projects/reap-new-update-model/v1-result/20260828-real7b-pell-success/code
OUT=/mnt/gloway/projects/lean-4.28-reap/out/pell-smoke

# ① mock policy/value 服务（OpenAI 兼容，只回 "trivial"）
python3 -m cpu_runtime.mock_services --port 18080 >"$OUT/mock.log" 2>&1 &
sleep 1

# ② 跑单会话（成功性不要求：预期 exhausted；流程与 JSONL 必须完整）
cd "$V1"
python3 -m cpu_runtime.batch_solver \
  --manifest /mnt/gloway/projects/10-4-alpha-proof/lean/pell_smoke/manifest.example.jsonl \
  --project-dir /mnt/gloway/projects/lean-4.28-reap/runtime \
  --output-dir "$OUT/sessions" \
  --concurrency 1 --timeout-seconds 300

# ③ 归一化观察事件
python3 -m cpu_runtime.normalize_rollout --sessions-dir "$OUT/sessions" --output "$OUT/trajectories.jsonl"
```

验收（flow）：
- `sessions/pell-smoke-mock-01/result.json` 存在且 `status ∈ {exhausted, …}`，字段 schema 为 `reap.training.result.v1`；
- `progress.jsonl` 非空；`trajectories.jsonl` 非空（训练事件可被 `update_offline` / `update_online` 消费）。

## 3. 真实策略（云 GPU）流程

把 manifest 的 `policy_base_url` / `value_base_url` 换为云上服务（旧格式示例：
`http://<host>:<port>/sessions/<session_id>/policy/v1`），并保证：
- policy 服务对 `/chat/completions` 返回 `choices[].message.content` 与 `logprobs.content[].logprob`（reap 先验需要）；
- value 服务返回 JSON `{"score": ...}`（搜索用 `V=-score`；对齐版建议直接返回 `V=-d̂`，`d̂` 为 64 桶期望）。

当前本仓库未包含可用的 7B policy HTTP 服务实现；`scripts/cloud_real_smoke.py`
已验证模型+LoRA+值头在云端可加载、可前向、可更新。把服务接上后，本文件的
`reapTrainingMCTS` 即可产出真实搜索轨迹并接入两套更新。

## 4. 实测记录（2026-10-04，mock 策略）

环境：`/mnt/gloway/projects/lean-4.28-reap`（Lean 4.28.0-rc1 + Reap@0090d73 + v1 补丁 + 对齐补丁，`lake build` 7913 jobs 成功）。

```text
会话：pell-smoke-mock-01
结果：result.json → {"solved": false, "status": "exhausted", "schema_version": "reap.training.result.v1"}
过程：progress.jsonl 16 条（step 0..16，max_nodes/max_steps=16）
产物：raw_tree.json / wall_clock.jsonl / stderr.log / stdout.log 齐全
归一：normalize_rollout → trajectories2.jsonl（17 条 training.transition.v1 事件，tactic "trivial" 被内核拒绝）
```

说明：mock 策略只回 `trivial`，因此搜索必然 exhausted；本次验证的是
**真实内核执行 + 观察器/JSONL + 归一化**链路。接上能产出有效 tactic 的策略
（脚本化 mock 或云端 7B 服务）即可得到 `solved: true` 与 `proof_script`。

## 5. 求解记录：课程第一课已解出（2026-10-04）

策略：`scripts/scripted_policy_server.py`（测试夹具）返回单条 tactic：

```text
solve | intro x y h; simp only [step]; nlinarith [h]
```

结果（`out/pell-smoke/sessions-invariant2/pell-invariant-01/result.json`）：

```json
{"solved": true, "status": "solved",
 "proof_script": "solve | intro x y h; simp only [step]; nlinarith [h]",
 "schema_version": "reap.training.result.v1"}
```

要点：
- 该 tactic 经内核执行 → `checkProofScript` → `replaySolvedNode` → `checkProof` 全部通过；
- `normalize_rollout` 产出 5 条事件（3 × tactic_eval、1 × tree_edge、1 × session_result）；
- **约束**：`evalTacticStr` 每次只接受**一条** tactic，顶层 `;` 序列会 parseError；
  多步证明请写成单条组合子（如 `solve | t1; t2; t3` 或 `exact ... by t1; t2`）。

复现命令：

```bash
python3 scripts/scripted_policy_server.py --port 18081 \
  --tactic "solve | intro x y h; simp only [step]; nlinarith [h]" &
# 然后用 manifest.invariant.jsonl 跑 batch_solver（同上 §2 的 ②③）
```

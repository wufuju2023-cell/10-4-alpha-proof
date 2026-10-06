# Lean/Reap 4.28 真实验证链路

本 workstream 把 FATE-M 20×200 的权威 `formal_statement` 接到真实 Reap
MCTS 和 Lean kernel。它是两条训练臂共用的 actor/verifier 边界，不使用
mock 环境，也不把 self-reported `solved` 当作正样本。

## 当前落实的安全边界

- 输入逐条校验数据集内 SHA-256，并固定顶层 `problems.jsonl`、生成 theorem、
  模型、Reap runtime receipt、Mathlib manifest 和两个 `lake` 可执行文件。
- Reap wall-clock 按真实格式解析 `extra.result` 的 raw JSON string；null、坏
  JSON、空 choices 或类型伪造均 fail closed。
- session 的 observer、tree、service receipts、policy state 和 checkpoint ACK
  全部隔离。checkpoint 模式是同步 barrier；ACK 和 coordinator receipt 均绑定
  session/tree/step/前后 policy version，发布使用原子 rename。
- session-aware HTTP proxy 固定 policy sampling 为 temperature=1.5、top_p=0.9、
  max_tokens=256、n=64，并为每次 policy/value 请求记录终态 receipt。只要存在
  失败、缺失或计数不符，轨迹就是 `indeterminate`，绝不会作为负样本。
- 正成功需要第二个 Lean 进程在纯 Mathlib 课程项目里重放。重放命令固定为
  `lake env lean --json -E hasSorry`，同时注入 `warningAsError`、显式扫描
  `sorry`/`admit`/holes、强制 timeout，并绑定 theorem/stdout/stderr/工具链哈希。
- verifier 在签名前从有序连续的 verified selected path 独立推导 value target：长度
  `L` 的第 `i` 步必须是 `-(L-i)`。actor 自报值只是一致性字段；错标、逆序、缺失、
  重复索引或超过 64 步都会在启动 Lean/签名前 fail closed。
- verifier 只接受 unsigned `canonical_search_receipt_v2` actor envelope；先校验
  request/statement/selected-path/state-chain，再用工作区外 Ed25519 私钥签名。
  evidence 会用冻结 verifier-lock 公钥复核签名。裸 `terminal_verified`、自哈希
  JSON、伪造 session 或重算 hash 但没有私钥的 receipt 均无效。

精确 verifier-lock 格式见 `docs/verifier_lock_v1.md`；canonical receipt 与
`../ce_arm/docs/receipt_v2.md` 完全一致。

## 目录

- `src/fate_reap/session_builder.py`：4000 条课程到 Reap theorem/manifest。
- `src/fate_reap/runner.py`：隔离并发进程、两阶段验证和证据汇总。
- `src/fate_reap/service_receipt_proxy.py`：session-aware policy/value 代理。
- `src/fate_reap/evidence.py`：fail-closed 分类和正/负奖励资格。
- `src/fate_reap/strict_replay.py`：独立 Lean 重放与签名 canonical receipt。
- `src/fate_reap/canonical_receipt.py`：request/path/state-chain 与 Ed25519 合约。
- `src/fate_reap/checkpoint.py`：原子 policy state/ACK 和运行后审计。
- `runtime/`：固定 Reap commit 的补丁和 Lean 训练 overlay。
- `scripts/prepare_runtime.sh`：固定 revision 构建并生成 runtime provenance。
- `tests/`：不下载 Lean/GPU 的格式、伪造、sorry、服务失败和并发隔离测试。

## 运行顺序

1. 在 UI 右上角检查 `/mnt/workspace` 持久盘；90 GiB 告警、95 GiB 禁止继续。
2. 完整 runtime 只由主线 bootstrap 执行一次；本 workstream 不另启下载。
3. 冻结并哈希 shared actor/budget/tokenizer config、模型资产和 verifier-lock；
   verifier 私钥放 `/run/secrets` 等工作区外路径。
4. 启动真实 policy/value 服务和 `service_receipt_proxy`。
5. shared actor 必须为每个 session 写 `$FATE_ACTOR_RECEIPT_PATH`，内容是尚无
   `verification` 的 canonical v2 envelope，包含真实 prompt/token IDs、tactic
   span 及连续 state hashes。
6. 先跑 1–2 题真实 model + real Lean smoke；签名 receipt 被 CE converter 与
   Online-v2 同时接受后，才允许扩大实验。

关键命令（所有 `$..._SHA256` 必须来自冻结 run manifest，不能现场猜）：

```bash
export PYTHONPATH="$PWD/src"

python -m fate_reap.service_receipt_proxy \
  --policy-upstream http://127.0.0.1:8000/v1 \
  --value-upstream http://127.0.0.1:8001/v1 \
  --receipt-root "$RUN_ROOT" --model-sha256 "$MODEL_SHA256"

python -m fate_reap.session_builder \
  --problems "$PROBLEMS" --expected-problems-sha256 "$PROBLEMS_SHA256" \
  --model-sha256 "$MODEL_SHA256" --output-dir "$BUILD/theorems" \
  --manifest "$BUILD/sessions.jsonl" \
  --policy-base-url 'http://127.0.0.1:8010/sessions/{session_id}/policy/v1' \
  --value-base-url 'http://127.0.0.1:8010/sessions/{session_id}/value/v1' \
  --variant-start 1 --variant-end 1 --families 1,2 \
  --num-samples 2 --max-steps 2 --max-goals 8

python -m fate_reap.runner \
  --manifest "$BUILD/sessions.jsonl" --theorem-root "$BUILD/theorems" \
  --problems "$PROBLEMS" --expected-problems-sha256 "$PROBLEMS_SHA256" \
  --project-dir "$REAP_PROJECT" --course-project "$COURSE_PROJECT" \
  --runtime-receipt "$RUNTIME_RECEIPT" \
  --expected-runtime-receipt-sha256 "$RUNTIME_RECEIPT_SHA256" \
  --expected-course-manifest-sha256 "$COURSE_MANIFEST_SHA256" \
  --lake "$REAP_LAKE" --expected-lake-sha256 "$REAP_LAKE_SHA256" \
  --strict-lake "$STRICT_LAKE" --expected-strict-lake-sha256 "$STRICT_LAKE_SHA256" \
  --verifier-lock "$VERIFIER_LOCK" \
  --expected-verifier-lock-sha256 "$VERIFIER_LOCK_SHA256" \
  --verifier-private-key /run/secrets/fate-verifier-ed25519.key \
  --workspace-root /mnt/workspace --output-dir "$RUN_ROOT" \
  --observer-mode record --concurrency 1
```

本地回归：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -v
```

当前本地完整套件为 **48 passed**，包括 value target 多步正例、单步伪造 `-64`、
逆序/缺失/重复索引、65 步拒绝，以及 signer 不可绕过校验的回归测试。

`prepare_runtime.sh` 不再隐式依赖 `PATH` 中存在裸 `lake`。解析优先级固定为
`LAKE_BIN`、`LEAN_BIN_DIR/lake`、实验目录
`runtime/toolchains/lean-4.28.0-linux/bin/lake`，缺失或不可执行时以 127 fail closed。
2026-10-05 远端旧脚本曾在 line 41 报 `lake: command not found`；后续 `pathfix5`
已验证修复有效：Reap 198 jobs 完成，`lake update` 的固定依赖全部恢复并进入
Mathlib post-update hooks。官方 `leantar` 0.1.16（`digama0/leangz` GitHub
release）最初下载时的 curl 56 unexpected EOF 已通过重新下载恢复；当前
`/root/.cache/mathlib/leantar-0.1.16.tar.gz` 为 2,362,400 bytes，tar 只读列表
校验通过。运行树仍在 `/tmp`，持久盘为 88.6%；下一安全动作是在取得“执行新下载
程序”的明确确认后，仅恢复 `lake exe cache get`，不重复 checkout/build 或把
runtime 搬入持久盘。完整状态见
`docs/remote_runtime_status_20261005.md`。

## Canonical candidate producer

`runtime/patches/0004-canonical-candidate-provenance.patch` 要求 controlled choice
携带 `raw_sample_index` 和 `service_candidate_sha256`。普通 content 原样执行（保留
前导空白）；think prefix 只允许移除一次，且 span 必须唯一、结束于 response content
边界，禁止 unanchored first-match。已有真实 64-choice receipt 为 64/64 可证明。
exact-action 去重合并完整 raw partition，同时保持旧
MCTS 的候选顺序、代表 draw、prior 和选择公式不变。

新 observer 事件为 `canonical_candidate`、`canonical_candidate_result` 和
`canonical_selected_path`。状态、proof script 与完整 executor-result payload 使用
运行时 `sha256sum`；工具缺失或输出非 canonical SHA-256 时 fail closed。终态 collector
只接受这些新事件和不可变 committed policy receipt，不从旧 `generation`/`eval`、
tree 文本或 action 相等关系补造事实：

`canonical_selected_path` 的 producer 改动独立固定在
`runtime/patches/0005-canonical-selected-path-producer.patch`：只有 proof script
抽取、独立 `checkProofScript`、根节点 replay 和最终 `checkProof` 全部成功后，Reap
执行器才从树边上的 `canonicalEventId` 提取路径并同步写 observer。空路径、分支型
AND path 或 observer 写入失败均 fail closed，且成功 `result.json` 必须在该事件之后
写出。`0005` 依赖 `0004` 的边身份和路径提取函数，不能单独或逆序应用。

仓内 `RolloutSink.lean` overlay 保留了历史混合换行（原始 SHA-256 为
`b9ee4243f972b0e5b1c7aecc9a2541e73c0d5ee5d491b168dec5952ad0a149c5`），而补丁为
LF。`prepare_runtime.sh` 复制 overlay 后只对目标 `RolloutSink.lean` 将 CRLF
确定性规范化为 LF，再进入补丁链；规范化后 SHA-256 为
`fc5dd2f31cba1ff26caef7a9c205d9c17c910fc0ae5258cf2e15cec213aa1379`，普通
`git apply` 应用 `0005` 后为
`3be945d506760552886d294e8f334bc2e02640278e6fcce96de3c62206d490d3`。其他 overlay
文件的字节不受该步骤影响。

```bash
PYTHONPATH=src python scripts/collect_candidate_facts.py \
  --observer SESSION/observer.jsonl --raw-tree SESSION/raw_tree.json \
  --result-json SESSION/result.json \
  --actor-receipt POLICY_REQUEST_1.json \
  --actor-receipt POLICY_REQUEST_2.json \
  --output SESSION/canonical_candidate_facts.json \
  --session-id SESSION_ID --tree-id TREE_ID --expected-raw-count 64
```

本地最小 Lean 编译已在固定 Reap commit、Lean `v4.28.0` 上通过：
`lake build Reap.Training`（198 jobs）。完整可复现入口仍是
`scripts/prepare_runtime.sh DEST --reap-only`；本次未刷新 integration bundle，也未启动
远端 runtime/实验。

collector 直接接受实际 `fate.policy_request.v2`，并验证 wrapper-bound executor hash、
parser/execution/status/terminal 交叉语义、selected proof 与 `result.json` 精确一致。terminal
candidate 的 executor commitment 同时绑定 selected-path receipt，因此 execution
attestation 不会丢掉 proof-script hash。当前 v2 sidecar 接受重复的
`--actor-receipt`，按 `generation_request_id` 将每份 committed receipt 精确绑定到一个
search state，并逐 request 验证完整 0..63 raw partition；selected path 可跨多个
request/state，且 parent/depth/successor-state hash 必须连续。多分支 AND 仍会明确
fail closed，focus 局部关闭只允许 `unresolved`。

## 不可变 real-E2E receipt join

`scripts/join_real_e2e_receipt.py` 对单个不可变 smoke 做两阶段处理：默认只审计；
只有 `--candidate-facts` 完整、绑定 observer/raw-tree/全部 policy receipt hashes，并且审计器从
全部原始 policy receipts、canonical observer、raw tree 与 `result.json` 独立重建出完全
相同的 sidecar 时，`--execute` 才会继续。envelope 使用内存中的重建值，不使用 sidecar
声明值；读取 Ed25519 私钥前还会重验全部输入文件 hash。任一 executor/successor hash
即使连同 sidecar 自哈希一起改写也会 fail closed 且不读私钥。通过后才构造 canonical
v2 multi-state envelope，先签
search-execution attestation，再调用现有 strict replay，最后把同一份签名 receipt
同时送入 CE 与 Online-v2 converters。tokenizer 只允许显式本地目录，脚本不会下载
模型；所有输出目录和 JSON 均拒绝覆盖。

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/join_real_e2e_receipt.py `
  --run-root <immutable-run> `
  --gap-report docs/real_e2e_join_gap.json
```

审计失败退出且报告固定包含 `private_key_read=false`、`lean_invoked=false`、
`signed=false`、`converted=false`。完整执行还必须显式提供本地 tokenizer、budget
config、problems/hash、course manifest/hash、lake/hash、runtime receipt hash、
verifier lock、工作区外私钥和新 output dir；`--help` 是权威参数表。

2026-10-05 权威真实 run `run-20261005T160105+0800-v2` 的机器可读结论在
`docs/real_e2e_join_gap_v2_20261005.json`（SHA-256
`6efa572d1a8c02a5551fbe1fc723a4b361d9a933ba9d9c80dc23780679bed012`）。第一遍
PASS 已归档为 `archive/superseded-first-pass-determinism`，其历史 gap 报告仍保留。
policy receipt 的 64 个
raw samples 与双 log-prob 完整，但 observer 只有 35 个去重后的 generation/eval
事件，缺少 raw-sample partition、逐样本 tactic span、稳定 execution/path IDs、
session-bound executor receipt hash、明确 successor hash/parent/depth，以及标准化的
verifier status/disposition/Lean execution count。因此该 run 没有被签名、重放或送入
任一 learner；从排序、文本或 raw tree 反推这些字段会伪造 executor facts。
字段逐项归属、sidecar schema 与最小 Lean/producer 补丁见
`docs/executor_candidate_facts_v1.md`。

## 尚缺、不得伪装为实验已开始

1. 真实 shared actor 已产生 64 条完整 raw completion/token/log-prob 收据，但 Reap
   producer 仍需在新 live run 发出 `canonical_candidate_facts.v2` 所需事件；旧 observer/raw
   tree 不能无损补造。本地 multi-request/multi-state collector/join 已实现并通过回归，
   尚未由真实 BestFirstSearch 运行验证。
2. 窄范围真实 7B + proxy + Reap + Lean 已通过；实际 actor prompt parity 仍需修正，
   随后必须完成 signed strict replay + 同一 receipt 的两臂 converter。
3. 还需 12 题代表性 smoke 与两臂对称单次更新/保存/恢复/回滚 smoke，才可放行
   完整 4000 题协议。

# Reap executor candidate-facts v1：字段归属与最小生产者补丁

## 结论

`canonical_search_receipt_v2` 不缺 policy/generation 证据；缺口在 Reap 把 64 个
raw draws 规范化、去重并执行之后，没有保留一份可验证的 provenance receipt。
`run-20261005T154901+0800` 的 policy receipt 有完整 64 条 raw token IDs、unwarped
与 sampling log-probs、seed、identity、generation commitment 和成本；observer 只有
35 对去重后的 `generation`/`eval` 事件。现有字段不足以证明每条 raw draw 映射到哪次
真实 Lean 执行，也不足以产生 canonical candidate/path。不能用文本相等、log-prob
排序、child index 或最终 tree 反推后再签名。

本文定义最小的新增 sidecar：`fate.reap.canonical_candidate_facts.v1`。它不是第四种
learner receipt；它只是 executor-owned join input，随后被纳入现有 canonical v2
envelope 和 execution attestation，learner 仍只接收 canonical v2。

## 当前证据可以直接加入的字段

下表中的“直接”表示原值可复制并由已有 hash 复核；“确定性派生”表示已有合约已
定义派生方式。没有既有 hash domain/ID recipe 的值不因“容易猜”而算确定性派生。

| canonical 字段 | 当前来源 | 结论 | 约束 |
|---|---|---|---|
| `problem_id` | `sessions.jsonl.source_id` | 直接 | 必须等于 report/result/policy session |
| `statement_sha256` | `sessions.jsonl.source_sha256` | 直接 | strict replay 再与 problems record 比对 |
| `actor_config_sha256` | policy receipt | 直接 | 已被 policy receipt hash 绑定 |
| `tokenizer_lock_sha256` | policy receipt | 直接 | converter 仍用同一 live tokenizer 复核 |
| `behavior_identity` | policy receipt | 直接 | behavior/base/tokenizer identity 已绑定 |
| `generation_contract` | policy receipt | 直接 | formal 值必须是 `1.5/.9/256/64` |
| `generation_request_id` / generation receipt hash | policy receipt | 直接 | committed receipt 已验证 |
| prompt / prompt token IDs / request seed | policy receipt | 直接 | 不重编码替换 |
| 64 个 raw token IDs、双 log-probs、finish reason、candidate hash、GPU/wall cost | policy receipt | 直接 | 顺序固定为 raw index `0..63` |
| `request_cost.prompt_tokens/generated_tokens/wall_seconds/gpu_seconds` | generation receipt | 确定性派生 | 必须等于 64 条 raw evidence 的精确和 |
| unique action text、prompt、goal/state key、search value | observer `generation` | 直接但仅供关联 | 当前没有 raw-index provenance，不能单独生成 candidate |
| evaluator result、child index、partial flag、Reap disposition | observer `eval` | 直接但语义未标准化 | `created/merged/eval_rejected` 不能直接当 canonical disposition |
| raw tree / root solved / proof script | checkpoint、raw tree、session result | 直接但仅为交叉检查 | 不能替代 candidate/path provenance 或 strict replay |
| verification section、value target | strict replay + canonical builder | 确定性派生 | value target 只由有序 verified path 长度派生 |

以下仍来自冻结运行协议，而不是 executor observer：`wave_index`、
`budget_config_sha256`、problems hash、runtime/course/lake/verifier-lock hashes。join 脚本
要求显式文件或 hash，不从 smoke 的 `max_steps` 等零散设置临时拼一个“budget”。

## 当前证据不能合法补出的字段

| 必需字段 | 为什么当前证据不够 | v1 producer 必须提供 |
|---|---|---|
| `sample_indices` | observer candidate index 是去重/排序后索引，不是 policy raw index | 该 unique action 对应的完整 raw-index partition |
| `sample_tactic_token_spans` | Reap 对文本 trim/规范化后丢掉 tokenizer 边界；前导空格会改变 BPE token | executor 从同一 choice token stream 证明每个 raw sample 的精确 `[start, stop)`；不可证明则拒绝 |
| `survivor_sample_index` | 去重执行后当前 observer 不记录实际代表 draw | 执行前固定并发出；不能在成功后挑一个更好看的样本 |
| `event_id`, `trajectory_id` | 当前无预先承诺的 ID recipe | 执行前生成的稳定 ID，并贯穿 result/path 事件 |
| `depth`, `parent_event_id` | node/child index 只能描述当时树位置，不能证明最终 proof parent chain | 执行事件的 logical parent/depth |
| `state_sha256`, `state_after_sha256` | 有 state text/key，但没有冻结 state hash domain；失败/merge 后的 successor 也不明确 | canonical state payload/hash和 `transition_applied` |
| `verifier_status` | `eval_result` 与 root solved 尚未映射到六个 canonical status | executor 明确输出 `verified_proof` / `invalid_tactic` / `unresolved` / `timeout` / `infrastructure_error` 等 |
| `execution_disposition` | Reap 的 `created/merged/ancestor_rejected/eval_rejected` 是搜索结果，不是 canonical `executed/parse_rejected` | parser phase 与 `did_execute` 决定的标准值 |
| `lean_tactic_executions` | 当前没有区分 parse rejection、已启动 tactic、merge | 精确 `0/1`；不得从 wall time 猜 |
| `executor_receipt_sha256` | 当前 observer event 没有定义“executor receipt”的 canonical hash domain | 对完整 candidate-result receipt 的 SHA-256，且绑定 session/tree/request/event |
| `selected_event_ids` | proof script/tree 可以显示 `tauto` 成功，但文本不等于稳定 event identity | 从实际 terminal child 沿 parent chain 导出的有序 IDs |

## `canonical_candidate_facts.v1` 最小 sidecar

sidecar 顶层必须包含：

- `schema_version`, `session_id`；
- `observer_sha256`, `raw_tree_sha256`, `policy_receipt_sha256`；
- `state_id`, `state_sha256`, `initial_state_sha256`；
- `candidates[]`，每项包含 join 脚本当前校验的 15 个 executor 字段；terminal candidate
  的 executor hash 是 candidate-result 与 wrapper-bound selected-path receipt 的组合承诺；
- `selected_event_ids[]`, `selected_proof_script_sha256`；
- 排除自身后 canonical JSON 的 `receipt_sha256`。

`candidates[*].sample_indices` 必须恰好分割 `0..63`，不缺、不重；每个 raw index
必须有一个 span；terminal selected candidate 必须是 `verified_proof`。sidecar 是待比对
的派生产物，不是签名前的事实来源：structural audit 必须从绑定的 policy receipt、
canonical observer、raw tree 和 `result.json` 重新运行同一个 collector 推导，并要求整个
对象逐字段相同。envelope 使用重建对象；私钥读取前再次校验原始输入和 sidecar 文件
hash。随后 execution attestation 对完整 search states、executor evidence、cost 和
outcome 签名，strict replay 再单独签 proof。

## 最小生产者修改

需要同时保留 policy provenance 和 Lean executor facts，但不需要修改 MCTS 算法或
learner 合约。

### 1. policy service 固定 raw identity；executor 证明 span

对每个 raw candidate，policy service 在 choice 的受控扩展字段中只返回
`raw_sample_index`、`service_candidate_sha256`。`tactic_token_span` 不属于 policy
response 合约，也不得由 prompt bridge 猜测。

- 普通 response 的实际 action 保持原始 `message.content`（包括前导空白），span 固定为
  从 token 0 到 response content 唯一结束边界；已有真实 64-choice 收据因此 64/64
  可证明。仅当 ASCII-trim 后以 `<think>` 开头时，允许移除恰好一个 think prefix，且
  action 保留 `</think>` 后的原始后缀空白；span 必须唯一且结束于同一个 content
  边界。禁止在整个 token 流中 first-match，避免绑定到思考段内的同文 tactic。
- collector 再从不可变 actor receipt 的原始 OpenAI response 独立重算该边界。
  任一 sample 无可证明的精确边界，则整个 formal request/sidecar fail closed；不得
  另行 trim、重编码、丢弃该 draw 或把它分配给另一个 action。

这一步解决当前 run 中前导空格被 Reap trim、因而无法证明 token span的问题。

### 2. `TacticGenerator`：去重时携带 provenance

把当前 `(tactic, premises, prior)` 临时 tuple 扩为包含：

`generation_request_id`, exact `action`, `raw_sample_indices`,
`sample_tactic_token_spans`, `survivor_sample_index`。

按 exact action 去重时合并 raw indices/spans；代表 sample 在执行前按固定规则选择并
记录。TreeSearch 不再用 observer 顺序或 log-prob 反向匹配 policy choices。

### 3. TreeSearch observer：新增两类事件

现有 `generation`/`eval` 可保留调试用途，新增：

1. `canonical_candidate`：在执行前发出 session/tree/request/event/trajectory、
   state-before、depth/parent、完整 raw mapping/spans、survivor、action 和 search value。
2. `canonical_candidate_result`：在 parser/evaluator/tree mutation 完成后发出同一个
   `event_id`、parser phase、`did_execute`、精确 Lean call count、完整 eval result、
   successor state payload/hash、canonical status/disposition 和 terminal flag。

状态映射必须在 executor 内完成：纯 parse error 是
`parse_rejected + invalid_tactic + 0`；已经启动 Lean tactic 的失败是
`executed + invalid_tactic + 1`；合法非终态是 `executed + unresolved + 1`；只有
proof check 通过且无 goals 才是 `executed + verified_proof + 1`；timeout 与
infrastructure error 分开，不能合并成普通负样本。`created/merged` 继续作为附加
search disposition，不能覆盖 canonical disposition。
固定 Reap 的 `tacticException` 是 tactic evaluation 通用异常，按已执行的
`invalid_tactic` 处理；只有 evaluator 外层/observer/runtime 中断才是 infrastructure error。

### 4. 搜索终止时发出实际 path

新增 `canonical_selected_path` 事件，内容为从 terminal child 沿已记录 parent event
链得到的 ordered event IDs、proof-script hash、terminal event ID 和 outcome。它必须
在最终 result 写出前产生；不允许 joiner 通过 action 文本在 tree 中搜索路径。
该事件先加入 session/tree/policy-version/sequence wrapper，再计算 selected-path
receipt hash。collector 要求其 proof script 与终态 `result.json.proof_script` 字节完全相同。

### 5. 终态 collector 原子发布 sidecar

runner 在 Reap 进程终止后校验 observer sequence、候选 start/result 一一配对、64 个
raw samples 完整分割、path chain 和 session/request bindings，然后把 sidecar以
exclusive-create + fsync/rename（或现有 hard-link publish）写出。sidecar 绑定最终
observer/raw-tree/policy file hashes。之后才允许 `join_real_e2e_receipt.py --execute`
读取工作区外私钥。

这个补丁面的核心只有 provenance-bearing generator record、两个 candidate events、
一个 selected-path event 和一个终态 collector；无需刷新 `integration_bundle`，也不
需要放宽 canonical span、signature 或 converter 校验。

### 当前明确限制

- v1 flat sidecar 仍只支持一个 committed policy request；正式多步搜索需要按 request
  分组的多 state sidecar/envelope，不能把不同 request 的 raw-index 空间拍平成一个数组。
- canonical selected path 仍是线性链。多分支 AND proof 不能安全线性化为 parent chain，
  runtime 现在明确 fail closed，而不是输出看似连续的伪路径。focus child 的局部关闭
  只能标为 `unresolved`；不得在最终全局 proof check 前标 `verified_proof`。

# 12-task representative real E2E smoke

Status: **task set and resource/evidence contract frozen; CPU-only real-Reap
capture entrypoint implemented; GPU launch blocked on captured root-state pins**.

This is a design and admission document. It does not claim that the 12-task
GPU smoke has run, and it does not authorize training.

## Frozen sample

The machine-readable authority is
`config/representative_smoke_plan.frozen.json`. It binds the audited
`problems.jsonl` SHA-256
`3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d`
and selects persisted families 1 and 20 at variants 1, 40, 80, 120, 160 and
175. In the data these are `fate_m_003_v*` and `fate_m_076_v*`, not family
numbers inferred from the external ID.

The fixed execution order alternates the two families at each increasing
variant. This exposes family-dependent failures early without changing the
12-task denominator.

## Required prompt path

The prior one-task script cannot simply be looped. Its Python
`build_root_state` helper is hard-coded to `fate_m_003_v001`, and independent
review established that its reported PromptManage hash was not the prompt
actually tokenized by the policy service.

The representative run must use the new point-of-use transform in
`InProcessPolicyService`:

1. Pinned Reap constructs its actual request with `TacticGenerator.mkPrompt`.
2. The service validates the incoming OpenAI request, then parses the actual
   root state from that incoming prompt.
3. A session-indexed callback verifies the parsed root-state SHA-256 against a
   pre-frozen pin and looks up the pinned formal prefix from the selected
   `problems.jsonl` row.
4. The callback invokes pinned
   `PromptManage.build_local_incontext_prompt_str(formal_prefix, root_state,
   related_theorems=None, template="qwen")`.
5. Only this returned actor prompt is tokenized/generated. The evolved
   `fate.policy_request.v2` receipt must bind
   the incoming prompt, actor prompt, both hashes, transformed token IDs and
   the normalized request.

No synthetic or hand-written root state is allowed. Before allocating a GPU,
run a CPU-only capture against the same pinned Reap/Lean runtime to collect
the 12 actual incoming root states. Review and freeze their hashes in a
separate immutable pin file; the GPU entrypoint must refuse a null, missing,
duplicate or mismatched pin.

The capture entrypoint is
`scripts/capture_representative_reap_prompts.py`. It imports neither Torch,
Transformers nor PEFT. It uses the real `session_builder` and pinned Reap
generator, records the point-of-use request, parses the root state, and returns
64 identical provenance-bearing `skip` actions. `skip` is deliberately
non-closing; Reap deduplicates the candidates and executes at most one search
expansion. Each capture is published before the HTTP response is released.
The final 12-task `root_state_pins.json` is written atomically only after every
session has exactly one capture and no process timeout.

Run on the prepared CPU/Lean host from the experiment root:

```bash
python workstreams/policy_service_bridge/scripts/capture_representative_reap_prompts.py \
  --problems data/problems.jsonl \
  --reap-project /tmp/fate-m-reap428/runtime \
  --lake runtime/toolchains/lean-4.28.0-linux/bin/lake \
  --output-dir /tmp/fate-m-reap-prompt-capture-12-20261005
```

`--problems` is explicit above. If omitted, the entrypoint resolves
`source.problems_relative_path` from the plan (`../../../data/problems.jsonl`,
the current experiment's `data/problems.jsonl`). In either mode the frozen
top-level SHA-256 and all 4,000 record hashes are still checked before Lean is
started; path resolution never substitutes for content validation.

The output path must be new and below `/tmp`. Nonzero Lean exit is retained as
process evidence and is acceptable for this deliberately non-closing probe;
missing capture, duplicate capture or timeout is not.

### Prompt-only bundle inputs

Preserve these experiment-relative paths in a remote prompt-only bundle:

- `data/problems.jsonl`;
- `workstreams/policy_service_bridge/config/representative_smoke_plan.frozen.json`;
- `workstreams/policy_service_bridge/scripts/capture_representative_reap_prompts.py`;
- `workstreams/policy_service_bridge/src/policy_service_bridge/`;
- `workstreams/shared_actor_bridge/src/shared_actor_bridge/` (required by the
  policy package's receipt imports even though this entrypoint never calls a
  model);
- `workstreams/lean_integration/src/fate_reap/session_builder.py` and its
  package `__init__.py`;
- the policy-service `tests/` when the bundle runs its local contract checks.

The prepared pinned Reap project and Lean `lake` binary are runtime inputs
passed by CLI; they are not silently discovered from a machine-specific
workspace path. The capture manifest records their hashes at point of use.

## Runtime shape

The implementation should generalize the existing
`scripts/real_reap_e2e_smoke.py` runtime instead of copying its model loader.
One process loads the pinned REAL-Prover base and the formal r16 adapter once,
starts one in-process policy service, and runs the 12 Lean sessions
sequentially. PEFT activation remains under the service's non-reentrant model
transaction lock. The logical session ID is separate from the single shared
adapter name; the active-session provider must verify that PEFT still has the
formal adapter active before returning the lock-protected logical session.

Each task gets its own directory beneath a new `/tmp` run root:

```text
sessions/<session_id>/
  observer.jsonl
  raw_tree.json
  result.json
  lean_result.json
  lean.stdout.log
  lean.stderr.log
  value_requests.jsonl
actor_receipts/<session_id>/policy_requests/<immutable receipt>.json
```

There is no training, optimizer, backward pass, adapter update, signed
training envelope, or `integration_bundle` work in this smoke.

## Bounds, progress and stopping

- Sequential sessions: concurrent Lean jobs would only queue on the one model
  lock and make failure accounting harder.
- One search expansion, 64 samples and at most 256 new tokens per task.
- 600 seconds per Lean process and 5,400 seconds total, including the single
  model load. A task timeout is an infrastructure failure, not a negative
  proof result.
- A timeout or model/service identity failure stops admission of new tasks.
  The supervisor first lets a committed in-flight request finish within the
  remaining total budget so its exact receipt cost is retained; if forced
  termination makes that impossible, the run is invalid and records that
  partial request cost as unknown rather than inventing zero.
- Every 20 seconds the parent emits and fsyncs a JSONL heartbeat containing
  stage, current session, completed/total, elapsed/remaining budget, per-GPU
  allocated/reserved/peak memory and utilization when available, committed
  prompt/generated-token totals, GPU seconds and latest aggregate token rate.
- A phase summary is emitted immediately after model load and after every
  session. `STATE.json` is atomically updated; terminal `DONE.json` or
  `FAILED.json` is immutable.

The hard token ceiling is 12 policy requests, 768 candidates and 196,608 raw
completion tokens. Capability success/failure does not adaptively shrink the
sample: all 12 tasks remain in the scientific denominator unless an
infrastructure stop invalidates the run.

## Exact accounting and admission checks

For every session, validate the evolved v2 receipt before accepting the task result and
require exactly one policy receipt, 64 candidates and exactly one persisted
value request. Aggregate prompt tokens, generated tokens, wall seconds and GPU
seconds only from validated committed receipts. Also report EOS and length
termination counts, Lean tactic executions, process elapsed time, solved
status and timeout status separately.

The batch is ready to launch only after all of these pass locally:

1. frozen task-selection test against the authoritative 4,000-row JSONL;
2. prompt parser/transform tests, including malformed envelope, wrong
   session, wrong root-state hash, empty/oversize transform and actual
   incoming-vs-actor prompt distinction;
3. mock 12-session test proving one loader call, one adapter load, isolated
   paths, exactly 12 receipts/value events and exact aggregate cost sums;
4. timeout test proving no later session starts and partial cost is never
   reported as zero;
5. syntax and the combined shared-actor/policy-service regression suites.

Until the root-state pin file exists and its 12 entries are reviewed, readiness is
**BLOCKED before GPU allocation**.

# Reap policy-service → canonical-v2 bridge

Status: **local code/mock integration complete; corrected official-prompt real 7B + Reap/Lean entrypoint ready to rerun; signed canonical-envelope join remains gated**.

This workstream closes the missing boundary identified in
`workstreams/reviews/lean_integration_review.md`: Reap's OpenAI-shaped policy
request is served by the *same live REAL-Prover + PEFT process* that owns the
actor. It does not start or download a model.

## What is implemented

- `InProcessPolicyService` accepts exactly Reap's
  `POST /sessions/{session_id}/policy/v1/chat/completions` shape (one user
  message). It requires the frozen `temperature=1.5`, `top_p=0.9`,
  `max_tokens=256`, `n=64`, `logprobs=true` contract in formal use. `top_p`
  may be absent because upstream Reap's `OpenAIChatRequest` has no such field;
  the normalized request still binds the frozen value. Conflicting values and
  unknown fields fail closed.
- An optional per-session `prompt_transform(session_id, incoming_prompt)` runs
  only after that request has passed validation. It must return one non-empty
  bounded string; exceptions and malformed results release no model output or
  receipt. The transformed string is the exact actor input. The integration-
  compatible `fate.policy_request.v2` receipt retains both strings and binds the normalized request
  hash, incoming prompt SHA-256, actor prompt SHA-256, and captured actor prompt
  token IDs. Seed derivation includes both the normalized request hash and actor
  prompt hash. Legacy immutable v2 receipts remain readable.
- It reuses `shared_actor_bridge.generate_raw_candidates` with the same model
  and tokenizer objects. The adapter preserves raw prompt/completion IDs,
  rescoring every raw completion token under the unwarped frozen behavior
  model. Sampling seed, exact generation parameters, live behavior adapter
  hash, pinned base identity and request cost are stored.
- Model generation/rescoring is process-serialized. This is required because
  changing the active PEFT adapter is process-global. HTTP requests and
  receipt allocation remain concurrent; each session has an isolated
  directory and monotonic request sequence. The service owns the shared
  model-transaction lock for the whole identity → activation → generation →
  rescore → identity interval. It passes `nullcontext` to the helper because
  the helper otherwise reacquires the lock; this supports ordinary
  non-reentrant locks without deadlock.
- The OpenAI response and the saved evidence are created from one in-memory
  token result. Each response choice's `message.content` is the stored
  `returned_text`, and every `logprobs.content` entry contains the corresponding
  raw `token_id` and unwarped log-prob. No code re-tokenizes response text to
  reconstruct rollout evidence.
- Each standard OpenAI choice also carries the controlled provenance extension
  `raw_sample_index` and `service_candidate_sha256`; receipt validation checks
  both against the full raw actor sample. No execution-tactic token span is
  emitted: Reap normalizes and deduplicates decoded tactic text after this HTTP
  boundary, so its executed substring cannot be mapped back to raw model tokens
  here without re-encoding ambiguity. The entire raw completion remains the PPO
  action; downstream search evidence must supply a validated span.
- A committed response is released only after an immutable, fsync'd receipt is
  atomically linked into place. The receipt binds the raw and normalized
  request, exact OpenAI response bytes, behavior identity, full raw IDs, both
  unwarped and sampling log-probs, sample identity hashes, seed, model/base,
  tokenizer and generation/actor config pins. A canonical generation-receipt
  payload and SHA-256 are nested inside the immutable, fsync'd request receipt.
  The downstream strict Reap replay signs the canonical envelope containing
  this generation commitment after it joins actual Lean/search facts. Identity
  drift, model failure or receipt publication failure returns no candidate
  content.
- `receipt_to_search_state` joins that immutable generation evidence with
  search/Lean-owned facts. It deliberately requires the caller to provide
  raw-sample mappings, tactic spans/actions, state hashes, Q values,
  parent/depth, verifier status, executor receipt hashes, disposition and Lean
  execution counts for *every* generated sample/candidate.
  It never invents those fields. The returned `SearchStateEvidence` can be fed
  to `shared_actor_bridge.build_unsigned_envelope`; strict Reap replay then
  adds the signed verification section.
- `/health` provides a lightweight liveness endpoint. Each request emits
  structured start, 20-second in-flight, commit or failure heartbeats.

## Wiring into the target repository

The audited target file is
`/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/src/10-4-alpha-proof/scripts/cloud_real_smoke.py`.
It already creates `tokenizer`, the 7B `model`, LoRA and the value head in one
process, but its search is `run_mock_search()` and its online old log-prob is
computed from a hand-built mock trajectory. That script is evidence of model
load/update only, not a real actor/Lean smoke.

The real smoke entrypoint should construct the service immediately after LoRA
setup and before starting Reap:

```python
from policy_service_bridge import (
    InProcessPolicyService, make_live_identity_provider,
    make_peft_active_session_provider, start_policy_server,
)
from shared_actor_bridge import GenerationParameters

model.eval()

def activate(session_id: str) -> None:
    # For one fixed adapter this may only assert the session. For multi-adapter
    # PEFT it must select exactly the requested adapter before returning.
    model.set_adapter(session_id)
    model.eval()

identity = make_live_identity_provider(
    model=model,
    policy_version=lambda sid: session_policy_versions[sid],
    base_version="REAL-Prover-fe76f68d",
    base_sha256=PINNED_BASE_ARTIFACT_SHA256,
    tokenizer_version=PINNED_TOKENIZER_REVISION,
    tokenizer_sha256=PINNED_TOKENIZER_LOCK_SHA256,
)
service = InProcessPolicyService(
    model=model,
    tokenizer=tokenizer,
    receipt_root=run_root / "actor_receipts",
    identity_provider=identity,
    activate_session=activate,
    active_session_provider=make_peft_active_session_provider(model),
    model_transaction_lock=shared_model_transaction_lock,
    generation=GenerationParameters(1.5, 0.9, 256, 64),
    seed_namespace=f"{experiment_id}:{paired_seed}",
    served_model_id="REAL-Prover-fe76f68d",
    actor_config_sha256=FROZEN_ACTOR_CONFIG_SHA256,
    tokenizer_lock_sha256=PINNED_TOKENIZER_LOCK_SHA256,
    prompt_transform=build_official_prompt_from_verified_reap_state,
)
server, thread = start_policy_server(service, host="127.0.0.1", port=8010)
# Reap policy endpoint:
# http://127.0.0.1:8010/sessions/{session_id}/policy/v1/chat/completions
```

Do not place a generic text-only OpenAI proxy between Reap and this endpoint;
that would lose the same-process identity guarantee. The existing
`lean_integration/service_receipt_proxy.py` remains useful for value-service
HTTP evidence, but its policy path is superseded by this service.

## Tests

From the workspace root:

```powershell
$env:PYTHONPATH = "experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/shared_actor_bridge/src;experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/src"
python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests
```

The policy-service tests cover exact response/raw-token correspondence, both raw log-prob
arrays and sample identities, frozen request validation, hash tampering,
exclusive publication, restart-safe sequences, two-session isolation under a
non-reentrant shared lock, behavior identity drift, health checks and the
generation-receipt → `SearchStateEvidence` join. They also cover strict Reap
prompt parsing, transform fail-closed behavior, actor-prompt seed binding, v2
prompt tamper rejection, and per-choice provenance.

The shared actor and policy-service suites run together with:

```powershell
python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/shared_actor_bridge/tests experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests
```

`tests/ReapResponseCompat.lean` was also executed with the pinned Reap
dependency runtime. It confirms that Reap's actual `OpenAIChatResponse`
`FromJson` accepts this response (including the hash-covered extra token IDs)
and sees two log-prob entries per fixture choice.

## Remaining external gates (not fabricated here)

1. Patch the target runtime to expose its actual session adapter activation and
   monotonically updated policy version to this service.
2. Run one bounded request against the real locally pinned REAL-Prover 7B and
   compare the receipt's response with Reap's parsed choice/log-prob sum.
3. Run the returned tactics in real Reap/Lean. Capture real state transitions,
   tactic spans and verifier evidence, then call `receipt_to_search_state`.
4. Build one unsigned canonical-v2 envelope, strict-replay it in a second Lean
   process, sign it, and pass the same signed object through both CE and
   Online-v2 converters.
5. The value endpoint remains separate. No value score or Lean result is
   invented by this workstream.

Until all five gates pass on ModelScope, this is not evidence that the formal
experiment can start.

## One-session real E2E smoke

`scripts/real_reap_e2e_smoke.py` composes this service with the audited
`shared_actor_bridge` loader and `fate_reap.session_builder`. It loads the
pinned REAL-Prover `fe76f68d...` base and frozen formal r16 adapter under the
PEFT name `fate_m_003_v001`, acquires a plain non-reentrant `threading.Lock`,
and serves the exact `temperature=1.5, top_p=0.9, max_tokens=256, n=64`
policy contract. A separate deterministic value-only endpoint returns
`{"score":0}`. The external source id is `fate_m_003_v001`; the generated
theorem path is taken from the immutable session manifest (the audited sample
currently maps to `P01/v001`), never inferred from that source-id number. The
smoke uses one Reap search step and `/tmp/fate-m-reap428/runtime`.

The smoke no longer uses the synthetic `build_root_state`. Its transform parses
the actual validated Reap request with an exact envelope grammar, verifies the
pinned root-state SHA-256, then calls the pinned
`PromptManage.build_local_incontext_prompt_str` with the problem's formal proof
prefix plus that actual state. A generator wrapper captures the real actor call;
the smoke fails unless the receipt actor prompt hash and token IDs match that
call and an independent tokenizer pass.

Run from the experiment root on the prepared GPU host (paths below are the
audited ModelScope layout):

```bash
python workstreams/policy_service_bridge/scripts/real_reap_e2e_smoke.py \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --adapter /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/assets/initial_lora_r16_a32_seed20261004 \
  --problems /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/data/problems.jsonl \
  --prompt-builder /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py \
  --reap-project /tmp/fate-m-reap428/runtime \
  --lake /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runtime/toolchains/lean-4.28.0-linux/bin/lake \
  --output-dir /tmp/fate-m-policy-e2e-fate_m_003_v001
```

The output directory must be new. It captures the immutable policy request
receipt, Reap observer stream, checkpoint raw-tree JSON, Lean stdout/stderr,
hash-bound result, and `DONE.json` or `FAILED.json`. Structured parent-process
heartbeats are flushed every 20 seconds. The model and both local HTTP servers
are released in `finally`; the smoke creates no optimizer and performs no
training. A timeout is bounded to 30 minutes by default.

This smoke exercises policy generation and real Lean search, but it does not
populate the search-owned mappings required by `receipt_to_search_state` for
every sample (tactic spans/actions, state hashes, values, executor receipts,
and disposition). Consequently it does not produce or sign a canonical-v2
envelope. The remaining gate is to join the immutable generation receipt with
those actual Reap/Lean facts, strict-replay and sign that envelope, then confirm
the same signed object is accepted by both CE and Online-v2 converters.

## CPU-only representative prompt capture

Before the 12-task GPU smoke, run
`scripts/capture_representative_reap_prompts.py` on the pinned Reap/Lean
runtime. It selects the frozen family 1/20 × variant
1/40/80/120/160/175 tasks, uses real `TacticGenerator.mkPrompt`, and returns
only a controlled provenance-bearing non-closing `skip` action. It never loads
the 7B model or formal adapter. Exact commands, output layout, bounds and the
GPU admission rule are in `REPRESENTATIVE_SMOKE_PLAN.md`; the GPU run remains
blocked until the resulting 12-entry `root_state_pins.json` is reviewed.

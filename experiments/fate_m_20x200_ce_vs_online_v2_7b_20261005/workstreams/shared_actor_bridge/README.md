# Shared actor canonical-v2 bridge

Status: **real 7B actor capture passed; Reap/Lean E2E smoke still required**.

This workstream closes the missing boundary without defining a fourth receipt
language.  Its top-level output is the existing `canonical_search_receipt_v2`
accepted by:

- `lean_integration/src/fate_reap/canonical_receipt.py` and strict Reap replay;
- `ce_arm/src/ce_arm/replay.py` after the Reap Ed25519 signature is attached;
- `online_v2_arm` through `to_online_v2_receipts`, which instantiates that
  package's reviewed `SearchStateReceipt`, `VerifierReceipt`, and
  `ProofPathReceipt` classes.

The additive `search_states` section is covered by the canonical envelope hash
and later by the Reap signature.  It carries complete same-state candidate
sets, exact prompt IDs, untouched raw completion IDs, tactic spans, action
masks, per-token frozen-behavior log-probs, behavior/base live identities,
generation parameters/seed, request costs, search values, and Lean-result
evidence hashes. Missing fields, token re-encoding drift, incomplete candidate
sets, mask/span drift, behavior identity drift, hash replacement, or signature
failure are rejected.

Each raw sample maps to one unique executed tactic result in the signed actor
envelope. Online-v2 expands every distinct raw token sequence into a
full-completion PPO row; byte-identical draws collapse only with explicit
multiplicity.

## REAL-Prover integration

The target repo's `scripts/cloud_real_smoke.py` already constructs the pinned
`AutoTokenizer` and `AutoModelForCausalLM`. Pass those *same live objects* to
`shared_actor_bridge.hf_adapter.generate_raw_candidates`; do not load another
tokenizer or add a new chat template. The adapter:

1. tokenizes the exact Reap prompt with `add_special_tokens=True`;
2. calls the live model's `generate` with the frozen `(temperature=1.5,
   top_p=0.9, max_new_tokens=256, n=64)` contract and recorded seed;
3. preserves the returned raw token IDs through first EOS;
4. rescans `prompt + raw completion` with the frozen behavior model to record
   both unwarped audit log-probs and temperature/top-p warped sampling
   log-probs per raw token; rescoring is
   padded and streamed with configurable `rescore_micro_batch`, without changing
   candidate order, seed, or token semantics;
5. verifies the behavior adapter hash both before and after the request.

Each candidate receives an equal allocation of the total generation **plus
rescoring** elapsed GPU/wall cost, so summing a request's candidate costs
recovers the measured request cost. The raw token count remains exact per
candidate.

A bounded capture-only real-model entrypoint is provided (it never downloads):

```bash
python scripts/smoke_real_hf_capture.py \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --output /tmp/fate-shared-actor-hf-smoke.json \
  --num-return-sequences 1 --max-new-tokens 32
```

It intentionally marks its report `formal_protocol=false`; formal rollout must
use the frozen `n=64, max_new_tokens=256` contract through the shared actor.

The Reap/search adapter must then supply only facts it owns: extracted tactic
span/action, successor-state hash, search Q value, verifier status/evidence,
depth/parent, and exact request cost. `build_unsigned_envelope` derives the
execution mapping, tactic spans, selected-path value distances, observed
raw-draw multiplicities, request hashes, and the immutable envelope hash.
Online-v2 derives the PPO mask over every raw completion token, including EOS
on stop. Multiplicity is a draw count, never a warped probability. Paths longer than the fixed 64-bin
value horizon are rejected at both build and validation; distances are never
clamped.

Each Online-v2 row records a deterministic raw-row event ID, the source Lean
execution event ID, exact raw token IDs, warped `q_old` log-probs, audit-only
unwarped `p_old` log-probs, and per-row finish reason. Mixed stop/length rows
therefore carry their own EOS convention. The proof path points to the raw row
containing each signed execution survivor and validates the original strict
Lean result. Tactic spans remain execution metadata and are never the PPO
action mask. CPU tests cover distinct raw completions mapped to one execution,
duplicate multiplicity, mixed EOS conventions, and shared execution binding.
The real 7B point-of-use receipt and Reap/Lean E2E smoke remain required before
formal training.

`write_immutable_envelope` publishes with exclusive-create semantics. Reap
strict replay reads that file, independently checks the proof with Lean and
adds its Ed25519 `verification`. Both converters require and cryptographically
verify this signature before returning learner inputs.

## Test

```powershell
python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/shared_actor_bridge/tests
```

The tests exercise the full unsigned-build → immutable-write → simulated Reap
signature → CE/Online-v2 conversion path and fail-closed mutations. They use no
model download or GPU.

## Remaining external gates

- The frozen-shape REAL-Prover capture is complete; see
  `../../runs/preflight/real-actor-capture/REPORT.md`. It used a deterministic
  smoke-only LoRA and no retrieval service, so the formal initial adapter and
  retrieved prompt inputs still need to be frozen.
- Wire the Reap policy service to return raw token IDs/log-probs (the current
  OpenAI-shaped proxy records only response hashes/text).
- Run one real tactic through Reap/Lean, strict replay-sign the envelope, then
  feed the same signed file through CE and Online-v2 converters.
- Confirm the executor's per-candidate status/evidence receipts are themselves
  bound to the Reap session. Until that E2E succeeds, formal training remains
  gated.

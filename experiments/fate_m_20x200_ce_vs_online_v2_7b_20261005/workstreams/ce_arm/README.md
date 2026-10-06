# CE arm: verified search-path cross entropy

Status: **one-command real 7B one-update + exact-resume smoke is implementation-ready; the signed real join receipt and remote lock binding are the remaining external inputs.**

This workstream prepares arm A of `fate_m_20x200_ce_vs_online_v2_7b_20261005`. It validates and splits the 20×200 curriculum, defines the exact actor→learner receipt boundary, converts strictly verified selected paths to the target repository's `Transition` schema, and provides a memory-bounded REAL-Prover 7B LoRA + CE64 learner.

## What “official CE” means here

The public `REAL-Prover` repository at commit `3e5987dc1009a7addc935bcd7b4ae777738a0848` contains inference/search code, but no AlphaProof actor/replay/learner implementation. The target repository at `e1b7ffe6feba8cbdb590d42836d750504aa86b94` is an **official-style reconstruction**: verified search path → masked token CE plus a 64-bin remaining-distance value loss. Therefore the scientifically accurate arm name is `official_search_path_ce`, not “the official released AlphaProof trainer.”

Search is held outside the arm-specific learner. Both CE and Online-v2 must consume rollouts made by the same target-repository actor, prompt, generator settings, Lean executor and budget. Upstream REAL-Prover defaults to best-first search when both search flags are false, while the target repository has a different aligned MCTS. Running upstream best-first only for CE would confound search with the update rule and is forbidden.

## Authoritative data flow

```text
immutable problems.jsonl (4000)
  -> prepare-course
  -> wave_001 ... wave_175 (20 families each)
  -> identical actor + real Lean for both arms
  -> canonical_search_receipt_v2
  -> frozen-course/current-wave allow-list
  -> trusted verifier-lock + request/path/state-chain hashes
  -> pinned REAL tokenizer prompt/completion/tactic-span validation
  -> selected proof path only
  -> atomically published replay bundle
  -> LoRA masked CE + 1e-3 * CE64
  -> per-wave atomic, manifest-hashed resumable checkpoint
```

The held-out `v176..v200` set is emitted separately and never enters replay. All curriculum propositions are proving tasks, so there is currently no independently verified “disproof” population. Timeouts, exhausted searches and infrastructure errors never become negative CE examples.

## Files

- `config/ce_arm.draft.json`: formal-run draft with deliberately null post-smoke hyperparameters.
- `config/ce_arm.real_7b_one_update.template.json`: numerically frozen one-update smoke profile; `bind-smoke-config` fills only content-hashed remote lock paths.
- `config/shared_actor.real_7b_smoke.json`: exact actor-config object in the current single-session launcher source (`receipt_identity_sha256=03c969254653fb877d2350d2146cba76ce7a8b893412625803cf2c2faea1a7c3`). Older captured receipts used a different actor identity and must not be mixed with this file.
- `src/ce_arm/course.py`: validates the exact 20×200 grid and statement hashes; derives 175 waves plus the 500-item held-out set.
- `src/ce_arm/replay.py`: schema-v2 receipt validation, course/wave allow-list and atomic replay-bundle publication. Legacy results are formally rejected.
- `src/ce_arm/train.py`: asset/config/replay/checkpoint verification, full RNG seeding, REAL-Prover 7B LoRA learner, strict target checks, and parent-hash-linked checkpoint resume/reconciliation.
- `docs/receipt_v2.md`: exact actor/verifier receipt contract.
- `docs/asset_lock.md`: required model/tokenizer/value-head/code/runtime lock contract.
- `LOCAL_READINESS.md`: latest local READY/BLOCKED decision, evidence, reproduction commands, and real-smoke gates.
- `tests/`: CPU regression tests, including held-out `v200`, future-wave and corrupt-token rejection.

## Commands

From this directory on either Windows or the remote instance:

```bash
python -m pip install -e . --no-deps
pytest -q

PROBLEMS=$(python ../../scripts/materialize_problems_jsonl.py)
fate-m-ce-data prepare-course \
  --source "$PROBLEMS" \
  --output /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/derived/course

fate-m-ce-data generate-tokenizer-lock \
  --model-root /mnt/workspace/models/REAL-Prover-fe76f68d \
  --model-revision fe76f68d9a88f342cb7b546307c20292fea9cced \
  --output /path/to/locks/tokenizer.lock.json

fate-m-ce-data generate-asset-lock \
  --model-root /mnt/workspace/models/REAL-Prover-fe76f68d \
  --model-revision fe76f68d9a88f342cb7b546307c20292fea9cced \
  --value-head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
  --initial-adapter /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/assets/initial_lora_r16_a32_seed20261004 \
  --target-repo /path/to/clean/10-4-alpha-proof \
  --target-value-head-source /path/to/clean/10-4-alpha-proof/alphaproof/net/value_head.py \
  --output /path/to/locks/assets.lock.json

fate-m-ce-data build-replay \
  --config config/ce_arm.frozen.json \
  --receipts /path/to/immutable/wave_receipts.jsonl \
  --output /path/to/derived/replay_wave_001 \
  --max-wave 1
```

## Shortest real one-update smoke

The joiner's immutable, pretty-printed `ce-receipt.json` is accepted directly;
do not rewrite it to JSONL. First bind the six real lock files to the checked-in
smoke template. The command refuses to overwrite an existing config and prints
the resulting SHA-256:

```bash
fate-m-ce-data bind-smoke-config \
  --template config/ce_arm.real_7b_one_update.template.json \
  --asset-lock /path/to/locks/assets.lock.json \
  --course-manifest /path/to/course/manifest.json \
  --shared-actor-config /path/to/shared-actor.frozen.json \
  --shared-budget-config /path/to/shared-budget.frozen.json \
  --tokenizer-lock /path/to/locks/tokenizer.lock.json \
  --trusted-verifier-lock /path/to/locks/verifier.lock.json \
  --output /path/to/run-inputs/ce-smoke.frozen.json
```

Then launch receipt admission, replay publication, exactly one CE optimizer
update, atomic checkpoint publication, and a second-process reload of that exact
checkpoint without another update:

```bash
fate-m-ce-smoke \
  --config /path/to/run-inputs/ce-smoke.frozen.json \
  --expected-config-sha256 HASH_PRINTED_ABOVE \
  --receipt /path/to/join-output/ce-receipt.json \
  --expected-receipt-sha256 HASH_FROM_JOIN_REPORT \
  --run-dir /path/to/runs/ce-real-7b-one-update \
  --wave-index 1
```

Success requires `SMOKE_DONE.json` with `global_step=1` and
`resume_verified=true`. Rerunning the same command is idempotent: it verifies and
reloads the already committed step instead of applying a second update.

After the real-model/real-Lean smoke fixes every null training/search field and changes config status to `frozen`:

```bash
fate-m-ce-train \
  --config config/ce_arm.frozen.json \
  --expected-config-sha256 HASH_FROM_IMMUTABLE_RUN_MANIFEST \
  --replay /path/to/derived/replay_wave_001 \
  --output /path/to/ce/run_seed_20261005 \
  --wave-index 1

# Resume the same immutable config/replay to a larger total step count.
fate-m-ce-train \
  --config config/ce_arm.frozen.json \
  --expected-config-sha256 HASH_FROM_IMMUTABLE_RUN_MANIFEST \
  --replay /path/to/derived/replay_wave_001 \
  --output /path/to/ce/run_seed_20261005 \
  --wave-index 1 \
  --resume /path/to/ce/run_seed_20261005/checkpoints/wave_001_step_000001_global_00000001 \
  --steps 2
```

`--allow-draft --steps 1` is permitted only for a one-update smoke. The trainer otherwise refuses a draft config.

## Corrections to the current target-repository path

1. `OfflineLearner.update` calls forward for every sample, accumulates every graph, and performs one final backward. Its `micro_batch_size` only slices a Python loop; it does not release graphs, so it is not a real memory-bounded micro-batch implementation for a 7B model. This workstream backpropagates each micro-batch with globally correct normalization.
2. A fresh CE run loads and hashes the byte-identical shared initial LoRA used by Online-v2. It no longer creates a new random LoRA from the config. The adapter directory, manifest, safetensors, config and trainable tensor-state hash are all bound by the CE config and asset lock before the first update.
3. Formal receipts preserve exact prompt IDs, raw completion IDs and the tactic token span. Replay admission and training both re-tokenize with the asset-locked REAL tokenizer; disagreement with the tactic sent to Lean is a hard failure. There is no formal text fallback.
   Actor-config and tokenizer receipt identities are explicitly separated from the byte hashes of their lock files. The binder derives the actor's canonical object digest and reads the independently validated tokenizer-manifest digest, while the lock-file hashes continue to protect the files themselves.
4. Upstream `success` and self-asserted `terminal_verified` are not accepted. The trusted verifier ID/lock, request, theorem statement, selected path, state chain, terminal state and kernel result are hash-bound in schema v2.
5. Value supervision is fixed to the verified linear selected path: for path length `L` and zero-based `step_index=i`, the converter independently derives `value_target=-(L-i)`. The actor field is only a signed consistency field; any mismatch, non-contiguous/reversed/duplicate index, broken state chain, or path longer than 64 tactics is rejected rather than trusted or clamped.
   The training loader repeats this derivation, requires the complete index set `0..L-1`, reconstructs the canonical selected path, and verifies the original Ed25519 `selected_path_sha256` commitment with the frozen verifier key/lock. It also re-derives every transition ID from the signed request/action/state fields. Deleting a row, shrinking `L`, and recomputing all JSONL/manifest self-hashes cannot forge a shorter path.
   Before consuming the verifier public key, training hashes the exact lock bytes against the frozen config; it repeats that check at point of use, so replacing the lock with an attacker key and re-signing the collapsed path is rejected.
6. The advertised 10% Mathlib SFT mixture is disabled (`sft_mix=0.0`) because no pinned, licensed, hash-verified SFT pool has been found. Enabling it for CE alone without freezing the external data would invalidate the A/B comparison.
7. Shared actor budgets are accumulated by problem across all receipt/attempt IDs. A second validly signed receipt cannot reset attempts, generated-token, or Lean-execution quotas. The published replay manifest contains the complete per-problem cost ledger and its hash.
8. Every checkpoint names its parent manifest hash. Startup scans the verified chain under a publication lock, removes only unpublished temporary directories, advances a stale/missing `latest.json` to the unique complete successor, and rejects branches or missing parents. Publication itself now performs a strict compare-and-swap while holding that lock: a successor's parent must equal the currently published manifest hash, a root is allowed only for a genuinely empty output, and only an exact same-name/same-lineage checkpoint may be adopted idempotently after the rename→`latest.json` crash window. A stale writer is rejected before model serialization.

## Still missing before the real smoke

- The shared actor/real Lean workstream must emit `canonical_search_receipt_v2` as specified in `docs/receipt_v2.md`.
- Lean 4.28.0/mathlib 4.28.0 must be built and a one/two-problem strict run must pass. Upstream REAL-Prover documents Lean 4.16 and cannot be assumed compatible.
- The smoke must measure prompt/action lengths and truncation, GPU memory, aggregate generation rate, tactic extraction and verification latency.
- The one-update smoke values are frozen in its dedicated template. Formal-run batch sizing and steps per wave remain intentionally `null` in `ce_arm.draft.json` until the real smoke measures memory and throughput.
- Define the Online-v2 learner-token rule, then precommit the CE steps-per-wave rule. The shared budget must explicitly freeze attempts, generated tokens, and Lean calls per problem; learner tokens and optimizer steps are reported rather than silently equated.
- Generate and pin the asset lock, course manifest, common actor config, common budget config, tokenizer lock and trusted verifier lock. Formal runs hard-fail until every path/hash is populated.
- A bounded checkpoint retention policy still needs the experiment orchestrator. Checkpoints are atomic and manifest-verified on every configured interval; this workstream deliberately does not delete them.
- Recheck ModelScope's top-right persistent-storage indicator immediately before the smoke; do not infer quota safety from `df`.

## Validation completed locally

- The authoritative local `problems.jsonl` loaded as exactly 4000 distinct cells (`fate_m_003_v001` through `fate_m_076_v200`) with every statement hash valid.
- `pytest -q`: **59 passed** (current local suite; no new GPU/Lean run by this workstream).
- Regressions cover derived multi-step targets at replay and trainer boundaries, a one-step actor-forged `-64`, missing/inconsistent/incomplete path provenance, direct JSONL hash tampering, reversed/missing/duplicate indices, 65-step rejection, held-out `v200`, future-wave leakage, false action token IDs, self-asserted verification, per-problem cumulative attempt/token/Lean budgets, partial replay publication, deterministic Torch sampling, corrupted/mixed checkpoints, checkpoint crashes before/after the directory rename, stale-latest recovery, and both sequential and truly overlapping same-parent publication counterexamples.

No GPU training or real Lean search was started by this workstream.

## Frozen 20x10 formal path

The executable short comparison no longer uses the legacy 3500/500 split.
`ce_arm.cli prepare-subset-course` publishes the 160 adaptation rows (v001-v008),
40 held-out rows (v009-v010), and eight balanced 20-family waves.  The checked-in
derived manifest is `../../data/ce_subset_20x10_course/manifest.json`; bind it to
`config/ce_arm.subset_20x10.formal.template.json` with `ce_arm.cli bind-config`.

Once a cumulative signed CE receipt bundle for a wave exists, run exactly one
accepted update with `python -m ce_arm.formal`.  Waves 2..8 require `--resume`
pointing at the preceding wave checkpoint.  The runner rejects any wave outside
1..8 and requires `global_step == wave_index` after the update.

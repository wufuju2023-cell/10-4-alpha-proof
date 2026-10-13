# CE arm local readiness audit (2026-10-05)

## Decision

- **Local implementation: READY for the smallest real 7B + real Lean smoke through `fate-m-ce-smoke`.**
- **Formal 20x200 launch: BLOCKED.** The draft config intentionally contains
  null search/training fields and no frozen asset/course/actor/budget/tokenizer/
  verifier lock paths or hashes. No real joined actor → Lean strict replay → CE
  replay receipt has yet been admitted by this learner.

No GPU training, remote command, or Lean search was run by this CE-side update.

The prior launch-path gaps are now closed locally: the joiner's pretty-printed
single `ce-receipt.json` is consumed without rewriting; a fresh learner must
load the byte-identical frozen shared initial adapter instead of creating a new
LoRA; and the smoke entry point performs exactly one update followed by a
second-process reload of the same checkpoint with no second optimizer step.

## Evidence checked

1. The authoritative curriculum file is
   `../../data/problems.jsonl.gz` (validated after decompression).
   Its SHA-256 is
   `3f702d1e5add11721867735c369e5e4736dfe4e4ae28674220bc8bef6dc8152d`.
   Strict parsing yielded 4,000 unique cells, 20 families × 200 variants,
   with 3,500 adaptation rows (`v001..v175`) and 500 held-out rows
   (`v176..v200`).
2. CE's canonical-v2 boundary matches the current shared actor and strict Lean
   converter: exact prompt/raw-completion/tactic-span token evidence, linear
   path-derived value targets, request/config/tokenizer hashes, signed cost and
   actor-envelope commitments, Ed25519 verifier lock binding, and strict proof
   outcome are checked. The cross-workstream join/bridge tests pass.
3. Replay admission rejects held-out/future-wave rows, duplicate transitions,
   forged token IDs, incomplete/relabelled paths, unsigned/self-asserted proof
   results, and cumulative per-problem attempt/token/Lean budget overruns.
4. Resume is parent-manifest-linked and lock-serialized. Publication performs a
   parent compare-and-swap against `latest.json`; rename-before-latest orphans
   are adopted only when unique, live temporary directories are protected, and
   stale concurrent writers/branches fail closed.
5. Each learner step reports selection hash, policy/value/total loss, gradient
   norm, samples, step and cumulative learner action tokens, elapsed time, and
   replay hash to flushed stdout; the same evidence is persisted in checkpoint
   receipts and wave DONE metadata. The experiment orchestrator must provide the
   20–30 second heartbeat, GPU metrics, elapsed/remaining budget, and durable log.
6. Storage safety is an external launch guard by design. Formal CE stages must
   run through the reviewed orchestrator using a fresh
   `modelscope_ui_top_right` observation, 90 GiB warning, 95 GiB hard stop,
   projected peak reservation, and checkpoint-boundary rechecks. The CE learner
   does not treat `df` as capacity authority and does not delete checkpoints.

## Reproduction commands

From the workspace root in PowerShell:

```powershell
$w = 'experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/ce_arm'
$env:PYTHONPATH = (Resolve-Path "$w/src")
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest "$w/tests" -q
```

Result: `47 passed`.

```powershell
$root = (Resolve-Path 'experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005').Path
$env:PYTHONPATH = (@(
  "$root/workstreams/ce_arm/src",
  "$root/workstreams/shared_actor_bridge/src",
  "$root/workstreams/policy_service_bridge/src",
  "$root/workstreams/lean_integration/src",
  "$root/workstreams/online_v2_arm/src"
) -join [IO.Path]::PathSeparator)
python -B -m pytest `
  "$root/workstreams/lean_integration/tests/test_e2e_receipt_join.py" `
  "$root/workstreams/shared_actor_bridge/tests/test_bridge.py" -q
```

Result: `21 passed`.

```powershell
$o = 'experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/orchestration'
python -B -m pytest "$o/tests/test_orchestrator.py" -q -k storage
```

Result: `1 passed, 17 deselected`.

## Remaining real-smoke gates

1. Produce one real canonical-v2 receipt using the pinned REAL-Prover 7B,
   shared policy-service proxy, real Reap/Lean 4.28 strict verifier, and the
   exact current bridge/converter; admit it through `build-replay`.
2. Generate the six remote lock files, then bind them to
   `config/ce_arm.real_7b_one_update.template.json`. The smoke's actor/search,
   replay, sequence, batch/micro-batch, learning-rate and one-step values are
   already fixed; `bind-smoke-config` emits the final config SHA-256.
3. Run one update with the real model/tokenizer/value head and real replay.
   Confirm zero truncation, target-module coverage, finite losses/gradients,
   ROCm peak memory, checkpoint + same-wave resume, and strict DONE evidence.
4. Create a formal orchestrator stage (the checked-in file is still an example),
   pin its inputs and success receipt, capture a fresh ModelScope UI storage
   observation, reserve projected checkpoint space, and keep usage below the
   90/95 GiB thresholds.
5. Only after the smoke passes, freeze the paired CE/Online-v2 run schedule and
   start wave 1. A smoke failure must leave formal status blocked.

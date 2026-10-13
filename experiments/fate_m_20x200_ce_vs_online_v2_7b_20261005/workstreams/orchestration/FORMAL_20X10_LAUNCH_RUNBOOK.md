# 20x10 formal paired launch / resume runbook

## Verdict

The frozen 20-family x 10-variant experiment **cannot be started directly by
rerunning the current scripts, including after an instance restart**.  The
data split and both one-update learner gates are ready, but the repository has
no production driver that closes the multi-wave lifecycle:

`current arm checkpoint -> arm-specific rollout -> Lean/sign/join -> learner
update -> durable checkpoint -> next wave -> locked held-out evaluation`.

This is the single remaining implementation gap.  Do not stretch
`run_wave001_bridge.sh` into a formal run: it is deliberately hard-coded to the
initial adapter, variant 1, one policy request, one attempt, 256 generated
tokens, one search step, and an ephemeral `/tmp` output.

## Frozen experiment boundary

- Protocol: `config/subset_20x10_protocol.frozen.json`
  (`sha256=164dd388414b592a3fa083099840712b939334954cdf95e1281e0afc8479e0a0`).
- Train rows: `data/subset_20x10_train.jsonl`, 160 rows, variants 1--8,
  (`sha256=b9e9c4abcf02693a85d5f794b766465f65ac2488ed15e96d6fd90aff452e6ab0`).
- Held-out rows: `data/subset_20x10_heldout.jsonl`, 40 rows, variants 9--10,
  (`sha256=a394465ef3e74666abea400496672ee97e838ddf4364c390d85362fb5d497c09`).
- One seed: `20261005`; eight 20-problem train waves; no held-out data may
  enter replay, gradient, checkpoint selection, or tuning.
- End-to-end hard cutoff: 21,600 seconds.  At the cutoff, persist the current
  transaction and write `INCOMPLETE`; never announce a winner from unequal
  partial arms.
- Persistent storage: the ModelScope top-right value is authoritative.  Refuse
  a new expensive stage at `>=90 GiB`; kill/checkpoint the stage at `>=95 GiB`.

## What is reusable now

- Frozen subset construction and `--check` validation.
- Patched Reap/Lean runtime recipe and strict replay/signing code.
- Actor `rescore_micro_batch=1` fix and the unchanged `5e-4` old-logprob gate.
- Same-source CE and Online-v2 receipt converters.
- Real CE update/checkpoint/deep-resume smoke.
- Real accepted Online-v2 update/rollback smoke.
- Reviewed process-tree and storage supervisor.

The active 20-family one-step run is throughput evidence only.  Unsolved
sessions are legitimate evaluation outcomes but the current proof-only join
cannot turn them into CE proof replay; the run is not a formal wave checkpoint.

## Restart semantics

`/mnt/workspace` survives an instance restart; `/tmp` does not.  Therefore:

1. Reuse only hash-verified persistent signed receipts, configs, checkpoints,
   ledgers, cost receipts, and terminal files.
2. Rebuild `/tmp/fate-m-reap428/{reap,runtime}` from the pinned commit and five
   patches, then regenerate `/tmp/fate-m-reap428/runtime_receipt.json`.
3. Do not attempt to resume `/tmp/fate-m-wave001-one-step-*`; restart that
   preflight from a new immutable directory if it is still wanted.
4. A formal resume must start from the last atomically committed *common wave*.
   If only one arm committed wave `k`, keep its evidence but resume the other
   arm before advancing either arm to `k+1`.
5. CE can resume from its verified `latest.json`.  The current Online-v2
   one-update runner can reconcile an interrupted update, but its checkpoint
   does not persist optimizer state for the next wave; the missing formal
   driver must add and verify that cross-wave state before launch.

## Minimal admission check

Run from the persistent experiment root after every instance restart:

```bash
set -euo pipefail
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
cd "$EXP"

python3 data/build_balanced_subset.py --check
test "$(sha256sum config/subset_20x10_protocol.frozen.json | awk '{print $1}')" = \
  164dd388414b592a3fa083099840712b939334954cdf95e1281e0afc8479e0a0
test "$(sha256sum data/subset_20x10_train.jsonl | awk '{print $1}')" = \
  b9e9c4abcf02693a85d5f794b766465f65ac2488ed15e96d6fd90aff452e6ab0
test "$(sha256sum data/subset_20x10_heldout.jsonl | awk '{print $1}')" = \
  a394465ef3e74666abea400496672ee97e838ddf4364c390d85362fb5d497c09

test -x runtime/toolchains/lean-4.28.0-linux/bin/lake
test -s /tmp/fate-m-reap428/runtime_receipt.json
test -d /tmp/fate-m-reap428/runtime/.lake
test -d /mnt/workspace/models/REAL-Prover-fe76f68d
test -s assets/initial_lora_r16_a32_seed20261004/adapter_model.safetensors
```

Immediately before launch, the browser controller must transcribe a fresh UI
reading; keep doing this every 20--30 seconds while GPU stages run:

```bash
python3 workstreams/orchestration/src/experiment_orchestrator.py storage-sample \
  --state-dir runs/formal-20x10/control \
  --used-gib "$FRESH_MODELSCOPE_UI_USED_GIB" --capacity-gib 100 \
  --source modelscope_ui_top_right
```

## Required production state machine

The missing driver should be one thin, resumable entry point, not another
learner implementation.  It must use the existing actor, join, CE, Online-v2,
and supervisor modules and atomically commit these units:

1. `baseline_eval`: all 40 held-out problems, no update.
2. For `wave=1..8`, in fixed order:
   - CE rollout of the 20 wave problems from the latest CE checkpoint;
   - strict Lean accounting/signing and CE replay publication;
   - CE update and persistent optimizer/value-head/adapter checkpoint;
   - Online-v2 rollout of the same 20 problems from the latest Online
     checkpoint with paired request seeds;
   - strict Lean accounting/signing and Online-v2 update;
   - persistent Online optimizer/value-head/adapter checkpoint;
   - paired budget receipt and `COMMON_WAVE_COMMITTED` marker.
3. Lock both final checkpoints.
4. `ce_final_eval` and `online_final_eval`: all 40 held-out problems under the
   same four-attempt caps, with no gradients.
5. Write `DONE` only after all three 40-task evaluations and all eight common
   train waves exist; otherwise write `INCOMPLETE` or `FAILED`.

The driver must enforce the protocol's four attempts and 512-token ceiling or
freeze a new protocol revision.  Current actor contracts accept 256 tokens and
the one-step bridge makes only one attempt, so silently launching it would not
execute the frozen protocol.

## Intended launch and resume commands

These commands become valid only after the production driver above exists and
its config contains no placeholders:

```bash
cd /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
python3 workstreams/orchestration/scripts/run_formal_20x10.py \
  --protocol config/subset_20x10_protocol.frozen.json \
  --run-root runs/formal-20x10/seed-20261005 \
  --hard-deadline-seconds 21600

# Same command after interruption/restart; foreign or forked checkpoints fail closed.
python3 workstreams/orchestration/scripts/run_formal_20x10.py \
  --protocol config/subset_20x10_protocol.frozen.json \
  --run-root runs/formal-20x10/seed-20261005 \
  --hard-deadline-seconds 21600 --resume
```

The production state-machine skeleton now exists at
`scripts/run_formal_20x10.py`, with the fail-closed recipe template at
`config/formal_20x10.production.template.json`.  It owns the 60-unit lifecycle,
the 21,600-second absolute deadline, ModelScope 90/95-GiB guard, atomic common
wave markers, artifact revalidation and resume.  It deliberately refuses the
template until the remaining full four-attempt/512-token rollout and held-out
evaluation commands are supplied and the config is frozen; it never upgrades
the existing one-step bridge into fake formal evidence.

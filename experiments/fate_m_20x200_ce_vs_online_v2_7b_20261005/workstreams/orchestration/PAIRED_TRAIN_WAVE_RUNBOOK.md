# Paired formal training wave launcher

The launcher begins after actor rollout and the hardened join. It does not
replace either stage. Both arms may use the same joined wave only when they
used the same behavior checkpoint (wave 1); later waves require arm-specific
join manifests.

## Wave 1: exact ModelScope command

After `/tmp/fate-m-formal-w001-join-v1/training-inputs/manifest.json` exists:

```bash
set -euo pipefail
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
JOIN=/tmp/fate-m-formal-w001-join-v1/training-inputs/manifest.json
CE_CONFIG="$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json"
test -f "$JOIN"
test -f "$CE_CONFIG"
mkdir -p "$EXP/runs/formal-20x10/wave_001"
TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 python3 \
  "$EXP/workstreams/orchestration/scripts/run_paired_train_wave.py" \
  --wave-index 1 \
  --ce-join-manifest "$JOIN" \
  --online-join-manifest "$JOIN" \
  --ce-config "$CE_CONFIG" \
  --run-root "$EXP/runs/formal-20x10" \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --target-repo "$EXP/src/10-4-alpha-proof" \
  --arm-order ce-first \
  2>&1 | tee -a "$EXP/runs/formal-20x10/wave_001/paired-train.log"
```

Deterministic terminals and checkpoints are under:

- CE: `$EXP/runs/formal-20x10/wave_001/ce/update/FORMAL_DONE.wave_001.json`
- Online-v2: `$EXP/runs/formal-20x10/wave_001/online-v2/update/DONE.json`
- common wave: `$EXP/runs/formal-20x10/wave_001/PAIRED_TRAIN_DONE.json`
- CE checkpoint lineage: `$EXP/runs/formal-20x10/ce-learner/checkpoints/`

Re-running the same command validates and skips completed arms. If Online-v2
has durable partial state, the native runner receives `--resume`.

The command is intentionally foreground and sequential. `subprocess.run`
waits for CE to exit (releasing its 7B CUDA allocation) before the Online-v2
config builder and learner start. Do not launch a second copy or overlap it
with a still-resident actor process on the same GPU.

Resource planning evidence: the local experiment record measures one 7B BF16
load at 167.522 seconds and inference peak allocation at 15,323,076,096 bytes.
Both formal learners retain micro-batch 1 and gradient checkpointing, and the
Online learner switches policy/behavior/base views on one backbone rather than
holding three base models. Thus more joined samples increase time, not batch
peak memory. Two model loads impose a measured lower-bound component of about
5.6 minutes. CE has one optimizer step. For `S` Online unique candidate
samples, two formal epochs execute `2S` backward micro-batches and at most
`13S` model forwards (old-logprob audit plus policy/behavior/base train and
post-step passes). A one-problem smoke had 10 unique events; `S≈200` is a
planning expectation, not a measured formal-wave result. Measure `S` from the
finished join before quoting an ETA.

Each arm saves one roughly 0.4–0.5 GiB LoRA/value/optimizer checkpoint; reserve
1.2 GiB persistent headroom for the paired wave. With the UI near 89.7/100 GiB,
remove at least 1.5 GiB of reproducible obsolete bundles before starting if the
below-90 GiB warning invariant is to be maintained. Never start/continue a new
checkpoint publication near the 95 GiB hard stop.

Once per completed join, obtain the exact Online workload without allocating
the 7B model:

```bash
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005 \
JOIN=/tmp/fate-m-formal-w001-join-v1/training-inputs/manifest.json \
PYTHONPATH=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/online_v2_arm/src \
python3 - <<'PY'
import json, os
from alphaproof_online_v2_arm.real_runner import load_join_receipts
m = json.load(open(os.environ['JOIN'], encoding='utf-8'))
w, _ = load_join_receipts(m['online_receipt_pins'])
n = len(w)
print(json.dumps({
    'online_samples': n,
    'microbatches_per_epoch': n,
    'backward_microbatches': 2 * n,
    'forward_pass_upper_bound': 13 * n,
    'max_sequence_tokens': max(x.input_ids.numel() for x in w),
    'action_tokens': sum(int(x.action_mask.sum()) for x in w),
}, sort_keys=True))
PY
```

One narrow pre-launch process gate is sufficient:

```bash
if pgrep -af '[r]eal_reap_wave_one_step.py|[c]e_arm.formal|[r]un_real_one_update.py|[r]un_paired_train_wave.py'; then
  echo 'refusing to overlap 7B actor/learner processes' >&2
  exit 3
fi
```

After the foreground command returns zero, this assertion independently
checks the scientific terminal conditions:

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path

root = Path('/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runs/formal-20x10/wave_001')
pair = json.loads((root / 'PAIRED_TRAIN_DONE.json').read_text())
ce = json.loads((root / 'ce/update/FORMAL_DONE.wave_001.json').read_text())
online = json.loads((root / 'online-v2/update/DONE.json').read_text())
assert pair['state'] == 'DONE' and pair['wave_index'] == 1
assert ce['status'] == 'complete' and ce['wave_index'] == 1 and ce['global_step'] == 1
assert online['state'] == 'DONE'
update = online['update_receipt']
assert update['accepted'] is True and update['optimizer_steps_this_update'] > 0
assert online['pre_policy_sha256'] != online['post_policy_sha256']
for terminal, expected in (
    (root / 'ce/update/FORMAL_DONE.wave_001.json', pair['ce']['terminal_sha256']),
    (root / 'online-v2/update/DONE.json', pair['online_v2']['terminal_sha256']),
):
    assert hashlib.sha256(terminal.read_bytes()).hexdigest() == expected
print(json.dumps({
    'paired': 'PASS', 'ce_global_step': ce['global_step'],
    'online_samples': update['samples'],
    'online_epochs': update['epochs_completed'],
    'online_optimizer_steps': update['optimizer_steps_this_update'],
    'online_gpu_peak_bytes': online['gpu_peak_allocated_bytes'],
}, sort_keys=True))
PY
```

## Wave 2 and later

Generate separate actor/join evidence with each arm's current checkpoint, then
change only the wave number and manifest paths:

```bash
WAVE=2
CE_JOIN=/tmp/fate-m-formal-w002-ce-join-v1/training-inputs/manifest.json
ONLINE_JOIN=/tmp/fate-m-formal-w002-online-join-v1/training-inputs/manifest.json
mkdir -p "$EXP/runs/formal-20x10/wave_002"
TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1 python3 \
  "$EXP/workstreams/orchestration/scripts/run_paired_train_wave.py" \
  --wave-index "$WAVE" \
  --ce-join-manifest "$CE_JOIN" \
  --online-join-manifest "$ONLINE_JOIN" \
  --ce-config "$CE_CONFIG" \
  --run-root "$EXP/runs/formal-20x10" \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --target-repo "$EXP/src/10-4-alpha-proof" \
  2>&1 | tee -a "$EXP/runs/formal-20x10/wave_002/paired-train.log"
```

Wave 2+ automatically and fail-closed loads the previous common wave's CE
checkpoint and Online-v2 adapter, value head, optimizer/scheduler/RNG state.
Odd waves run CE first; even waves run Online-v2 first.

## Important CE input fact

The frozen course is an allow-list/split manifest, not a policy-label dataset.
The current official CE implementation therefore **cannot train directly from
the frozen course alone**. It needs canonical signed search receipts whose
verified proof outcome carries `selected_path` tactic labels. Unsolved receipts
are admitted for accounting but yield zero CE transitions. The launcher keeps
the cumulative signed receipt stream and never invents labels from statements.

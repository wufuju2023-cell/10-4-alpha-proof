# Wave 002 paired rollout and join

This starts only after the existing paired-train launcher has published:

- `runs/formal-20x10/wave_001/PAIRED_TRAIN_DONE.json`
- `runs/formal-20x10/wave_001/ce/update/FORMAL_DONE.wave_001.json`
- `runs/formal-20x10/wave_001/online-v2/update/DONE.json`

The CE and Online-v2 actor adapters are read from those separate terminals.
They must not be replaced by the shared initial adapter. Root-state capture is
shared because it is model-independent. CE's CPU-only join overlaps the Online
actor rollout so the GPU is not left idle between arms.

```bash
set -Eeuo pipefail
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
RUN_ROOT="$EXP/runs/formal-20x10"
WAVE1="$RUN_ROOT/wave_001"
CONTROL="$RUN_ROOT/control/wave_002"
PAIR_ROOT="$RUN_ROOT/rollouts/wave_002"

test -f "$WAVE1/PAIRED_TRAIN_DONE.json"
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
  "$WAVE1/PAIRED_TRAIN_DONE.json")" = DONE
mkdir -p "$CONTROL"

PLAN="$CONTROL/plan.json"
python3 "$EXP/workstreams/orchestration/scripts/prepare_formal_wave_plan.py" \
  --problems "$EXP/data/problems.jsonl" --wave-index 2 --output "$PLAN"

CAPTURE="/tmp/fate-formal-wave002-root-state-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$EXP/workstreams/policy_service_bridge/scripts/capture_representative_reap_prompts.py" \
  --problems "$EXP/data/problems.jsonl" --plan "$PLAN" \
  --reap-project /tmp/fate-m-reap428/runtime \
  --lake "$EXP/runtime/toolchains/lean-4.28.0-linux/bin/lake" \
  --output-dir "$CAPTURE" --per-session-timeout-seconds 300 \
  --total-timeout-seconds 7200 --heartbeat-seconds 20
PINS="$CAPTURE/root_state_pins.json"

CE_DONE="$WAVE1/ce/update/FORMAL_DONE.wave_001.json"
ONLINE_DONE="$WAVE1/online-v2/update/DONE.json"
CE_CKPT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["checkpoint"])' "$CE_DONE")
ONLINE_CKPT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["checkpoint"]["path"])' "$ONLINE_DONE")
CE_ADAPTER="$CE_CKPT/adapter"
ONLINE_ADAPTER="$ONLINE_CKPT/adapter"
test -s "$CE_ADAPTER/adapter_model.safetensors"
test -s "$ONLINE_ADAPTER/adapter_model.safetensors"

BOOTSTRAP_JSON=$(find "$EXP/runs/formal" -maxdepth 2 -name bootstrap.json -type f \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
test -f "$BOOTSTRAP_JSON"

bash "$EXP/workstreams/orchestration/scripts/run_paired_wave_rollout_join.sh" \
  2 "$CE_ADAPTER" "$ONLINE_ADAPTER" "$PLAN" "$PINS" "$PAIR_ROOT" "$BOOTSTRAP_JSON"
```

Successful terminals:

```bash
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
  "$PAIR_ROOT/ce/join/DONE.json")" = DONE
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
  "$PAIR_ROOT/online_v2/join/DONE.json")" = DONE
```

Then run the existing paired trainer for wave 2:

```bash
CE_CONFIG="$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json"
mkdir -p "$RUN_ROOT/wave_002"
PYTHONUNBUFFERED=1 python3 \
  "$EXP/workstreams/orchestration/scripts/run_paired_train_wave.py" \
  --wave-index 2 \
  --ce-join-manifest "$PAIR_ROOT/ce/join/training-inputs/manifest.json" \
  --online-join-manifest "$PAIR_ROOT/online_v2/join/training-inputs/manifest.json" \
  --ce-config "$CE_CONFIG" --run-root "$RUN_ROOT" \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --target-repo "$EXP/src/10-4-alpha-proof" \
  2>&1 | tee "$RUN_ROOT/wave_002/paired-train.log"
```

Do not consume a join directory without its `DONE.json`. The current hardened
join is proof-only and deliberately fails closed if any selected actor root is
`VALID_UNSOLVED`; failed attempts remain in the actor evidence and are never
silently promoted to verified training receipts.

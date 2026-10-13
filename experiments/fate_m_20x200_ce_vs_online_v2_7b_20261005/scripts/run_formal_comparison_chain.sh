#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1

EXP=${FATE_EXPERIMENT_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
RUN_ROOT=${FATE_RUN_ROOT:-"$EXP/runs/formal-20x10"}
HELDOUT=${FATE_HELDOUT_ROOT:-"$RUN_ROOT/heldout-eval"}
JOIN=${FATE_JOIN_MANIFEST:-/tmp/fate-m-formal-w001-join-v2/training-inputs/manifest.json}
MODEL=${FATE_MODEL_ROOT:-/mnt/workspace/models/REAL-Prover-fe76f68d}
TARGET_REPO=${FATE_TARGET_REPO:-"$EXP/src/10-4-alpha-proof"}
CHAIN_DIR="$RUN_ROOT/chain"
mkdir -p "$CHAIN_DIR"

failed() {
  rc=$?
  python3 - "$CHAIN_DIR/FAILED.json" "$rc" "${BASH_LINENO[0]:-unknown}" <<'PY'
import json, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "schema_version": "fate.formal_comparison_chain.failure.v1",
    "exit_code": int(sys.argv[2]),
    "line": sys.argv[3],
    "unix_time": time.time(),
}, sort_keys=True) + "\n", encoding="utf-8")
PY
  exit "$rc"
}
trap failed ERR

heartbeat() {
  python3 - "$1" <<'PY'
import json, sys, time
print(json.dumps({"event": "chain_heartbeat", "stage": sys.argv[1], "unix_time": time.time()}, sort_keys=True), flush=True)
PY
}

if [[ -f "$CHAIN_DIR/DONE.json" ]]; then
  cat "$CHAIN_DIR/DONE.json"
  exit 0
fi

while [[ ! -f "$JOIN" || ! -f "$HELDOUT/initial/DONE.json" ]]; do
  if [[ -f "$HELDOUT/initial/FAILED.json" || -f "$HELDOUT/initial/INCOMPLETE.json" ]]; then
    echo "initial held-out evaluation reached a non-success terminal state" >&2
    exit 20
  fi
  heartbeat waiting_for_join_and_initial_eval
  sleep 20
done

heartbeat paired_training
if [[ ! -f "$RUN_ROOT/wave_001/PAIRED_TRAIN_DONE.json" ]]; then
  python3 "$EXP/workstreams/orchestration/scripts/run_paired_train_wave.py" \
    --wave-index 1 \
    --ce-join-manifest "$JOIN" \
    --online-join-manifest "$JOIN" \
    --ce-config "$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json" \
    --run-root "$RUN_ROOT" \
    --model "$MODEL" \
    --target-repo "$TARGET_REPO" \
    --arm-order ce-first 2>&1 | tee "$RUN_ROOT/wave_001/paired-train.log"
fi
test -f "$RUN_ROOT/wave_001/PAIRED_TRAIN_DONE.json"

heartbeat ce_final_eval
if [[ ! -f "$HELDOUT/ce_final/DONE.json" ]]; then
  bash "$EXP/scripts/launch_heldout_eval.sh" ce_final "$RUN_ROOT/wave_001/ce"
fi
test -f "$HELDOUT/ce_final/DONE.json"

heartbeat online_final_eval
if [[ ! -f "$HELDOUT/online_final/DONE.json" ]]; then
  bash "$EXP/scripts/launch_heldout_eval.sh" online_final "$RUN_ROOT/wave_001/online-v2"
fi
test -f "$HELDOUT/online_final/DONE.json"

heartbeat summarize
python3 "$EXP/scripts/summarize_heldout_comparison.py" \
  --initial "$HELDOUT/initial" \
  --ce-final "$HELDOUT/ce_final" \
  --online-final "$HELDOUT/online_final" \
  --output-dir "$HELDOUT/comparison"
test -f "$HELDOUT/comparison/DONE.json"

python3 - "$CHAIN_DIR/DONE.json" <<'PY'
import json, pathlib, time
pathlib.Path(__import__('sys').argv[1]).write_text(json.dumps({
    "schema_version": "fate.formal_comparison_chain.done.v1",
    "unix_time": time.time(),
}, sort_keys=True) + "\n", encoding="utf-8")
PY
cat "$HELDOUT/comparison/comparison.json"

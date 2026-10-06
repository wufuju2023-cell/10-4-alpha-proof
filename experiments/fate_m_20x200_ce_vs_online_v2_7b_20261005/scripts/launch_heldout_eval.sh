#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 {initial|ce_final|online_final} CHECKPOINT_OR_RUN_ROOT" >&2
  exit 2
fi

LABEL=$1
CHECKPOINT=$2
case "$LABEL" in
  initial|ce_final|online_final) ;;
  *) echo "invalid checkpoint label: $LABEL" >&2; exit 2 ;;
esac

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
E=${FATE_EXPERIMENT_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
MODEL=${FATE_MODEL_ROOT:-/mnt/workspace/models/REAL-Prover-fe76f68d}
REAP=${FATE_REAP_PROJECT:-/tmp/fate-m-reap428/runtime}
LAKE=${FATE_LAKE:-$E/runtime/toolchains/lean-4.28.0-linux/bin/lake}
PROMPT=${FATE_PROMPT_BUILDER:-$E/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py}
H=${FATE_HELDOUT_ROOT:-$E/runs/formal-20x10/heldout-eval}

for file in \
  "$E/data/subset_20x10_heldout.jsonl" \
  "$H/root_state_pins.json" \
  "$PROMPT" \
  "$LAKE"
do
  test -f "$file" || { echo "missing required file: $file" >&2; exit 2; }
done
test -d "$MODEL" || { echo "missing model directory: $MODEL" >&2; exit 2; }
test -d "$REAP" || { echo "missing Reap project: $REAP" >&2; exit 2; }
test -e "$CHECKPOINT" || { echo "missing checkpoint/run root: $CHECKPOINT" >&2; exit 2; }

mkdir -p "$H/logs"
python3 "$E/scripts/evaluate_heldout_checkpoint.py" \
  --checkpoint-label "$LABEL" \
  --model "$MODEL" \
  --checkpoint "$CHECKPOINT" \
  --heldout "$E/data/subset_20x10_heldout.jsonl" \
  --root-state-pins "$H/root_state_pins.json" \
  --prompt-builder "$PROMPT" \
  --reap-project "$REAP" \
  --lake "$LAKE" \
  --output-dir "$H/$LABEL" \
  --lean-timeout-seconds 120 \
  --heartbeat-seconds 20 \
  2>&1 | tee -a "$H/logs/$LABEL.log"

test -s "$H/$LABEL/DONE.json"
python3 - "$H/$LABEL/DONE.json" "$LABEL" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["state"] == "DONE", value
assert value["checkpoint_label"] == sys.argv[2], value
assert 0 <= value["solved_count"] <= 40, value
assert 0.0 <= value["pass_at_4"] <= 1.0, value
assert 0.0 <= value["truncation_rate"] <= 1.0, value
print(json.dumps({"event": "heldout_eval_asserted", **value}, sort_keys=True))
PY

#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1

E=${FATE_EXPERIMENT_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
REAP=${FATE_REAP_PROJECT:-/tmp/fate-m-reap428/runtime}
LAKE=${FATE_REAL_LAKE:-"$E/runtime/toolchains/lean-4.28.0-linux/bin/lake"}
H=${FATE_HELDOUT_ROOT:-"$E/runs/formal-20x10/heldout-eval"}
LOG=${FATE_HELDOUT_CAPTURE_LOG:-/tmp/fate-m-heldout-capture.log}
PROBLEMS=${FATE_PROBLEMS_PATH:-}
if [[ -z "$PROBLEMS" ]]; then
  PROBLEMS=$(python3 "$E/scripts/materialize_problems_jsonl.py")
fi

mkdir -p "$H/logs"
if [[ ! -f "$H/heldout_prompt_plan.json" ]]; then
  python3 "$E/scripts/prepare_heldout_prompt_plan.py" \
    --problems "$PROBLEMS" \
    --heldout "$E/data/subset_20x10_heldout.jsonl" \
    --output "$H/heldout_prompt_plan.json"
fi

if [[ -f "$H/root_state_pins.json" && -f "$H/CAPTURE_DONE.json" ]]; then
  echo "heldout capture already complete"
  exit 0
fi

# capture_representative_reap_prompts.py intentionally requires a destination
# that does not exist yet.  `mktemp -d` used directly as --output-dir creates
# it first and makes argparse fail with exit code 2.  Allocate only the parent.
CAP_PARENT=$(mktemp -d /tmp/fate-heldout-prompt-capture-parent.XXXXXX)
CAP="$CAP_PARENT/capture"
echo "heldout capture output: $CAP"
python3 "$E/workstreams/policy_service_bridge/scripts/capture_representative_reap_prompts.py" \
  --problems "$PROBLEMS" \
  --plan "$H/heldout_prompt_plan.json" \
  --reap-project "$REAP" \
  --lake "$LAKE" \
  --output-dir "$CAP" \
  --per-session-timeout-seconds 180 \
  --total-timeout-seconds 3600 \
  --heartbeat-seconds 20 2>&1 | tee "$LOG"

cp "$CAP/root_state_pins.json" "$H/root_state_pins.json"
sha256sum "$H/heldout_prompt_plan.json" "$H/root_state_pins.json" > "$H/capture.sha256"
printf '{"state":"DONE","log":"%s"}\n' "$LOG" > "$H/CAPTURE_DONE.json"

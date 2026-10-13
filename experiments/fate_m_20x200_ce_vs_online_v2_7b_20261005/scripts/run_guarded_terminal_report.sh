#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
E=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
R="$E/runs/formal-20x10-corrected"
H="$R/heldout-eval/ce_final"
trap 'rc=$?; printf "{\"state\":\"FAILED\",\"exit_code\":%s}\n" "$rc" > "$R/control/guarded_report_FAILED.json"; exit "$rc"' ERR
test -s "$R/control/online_rollback_identity_audit.json"
while [[ ! -f "$H/DONE.json" ]]; do
  test ! -f "$H/FAILED.json"
  test ! -f "$H/INCOMPLETE.json"
  echo "{\"event\":\"guarded_report_heartbeat\",\"stage\":\"waiting_for_corrected_ce_eval\",\"unix_time\":$(date +%s)}"
  sleep 20
done
python3 "$E/scripts/summarize_guarded_terminal_outcome.py" --experiment-root "$E"
python3 "$E/scripts/collect_corrected_evidence.py" --experiment-root "$E" \
  --output "$E/build/corrected_final_evidence_20261006.zip"

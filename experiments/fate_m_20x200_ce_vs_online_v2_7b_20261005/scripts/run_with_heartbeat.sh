#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 5 || "$4" != "--" ]]; then
  echo "usage: $0 LABEL BUDGET_SECONDS LOG_PATH -- COMMAND [ARGS...]" >&2
  exit 2
fi

label=$1
budget_seconds=$2
log_path=$3
shift 4

mkdir -p "$(dirname "$log_path")"
start_epoch=$(date +%s)
timed_out=0

printf '[phase-start] label=%s epoch=%s budget_seconds=%s command=' \
  "$label" "$start_epoch" "$budget_seconds"
printf '%q ' "$@"
printf '\n'

set +e
stdbuf -oL -eL "$@" \
  > >(tee -a "$log_path") \
  2> >(tee -a "$log_path" >&2) &
child_pid=$!

while kill -0 "$child_pid" 2>/dev/null; do
  sleep 20
  now_epoch=$(date +%s)
  elapsed=$((now_epoch - start_epoch))
  remaining=$((budget_seconds - elapsed))
  if (( remaining < 0 )); then
    remaining=0
  fi
  gpu_summary=$(rocm-smi --showuse --showmemuse 2>/dev/null \
    | awk '/GPU use \(%\)|GPU Memory Allocated/{gsub(/[[:space:]]+/," "); printf "%s;", $0}' \
    || true)
  printf '[heartbeat] label=%s elapsed_seconds=%s remaining_budget_seconds=%s gpu="%s" aggregate_rate=unavailable\n' \
    "$label" "$elapsed" "$remaining" "$gpu_summary"
  if (( elapsed >= budget_seconds )); then
    timed_out=1
    kill -TERM "$child_pid" 2>/dev/null || true
    break
  fi
done

wait "$child_pid"
rc=$?
set -e

if (( timed_out == 1 )); then
  rc=124
fi
end_epoch=$(date +%s)
printf '[phase-end] label=%s epoch=%s elapsed_seconds=%s exit_code=%s timed_out=%s\n' \
  "$label" "$end_epoch" "$((end_epoch - start_epoch))" "$rc" "$timed_out"
exit "$rc"

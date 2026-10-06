#!/usr/bin/env bash
set -euo pipefail

receipt="${FATE_RUNTIME_RECEIPT:-/tmp/fate-m-reap428/runtime_receipt.json}"
real_lake="${FATE_REAL_LAKE:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runtime/toolchains/lean-4.28.0-linux/bin/lake}"
deadline=$((SECONDS + ${FATE_RUNTIME_WAIT_SECONDS:-1800}))
while [[ ! -s "$receipt" ]]; do
  if (( SECONDS >= deadline )); then
    printf 'runtime receipt did not appear: %s\n' "$receipt" >&2
    exit 124
  fi
  sleep 5
done
exec "$real_lake" "$@"

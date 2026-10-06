#!/usr/bin/env bash
set -Eeuo pipefail

experiment_root=${1:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
runtime_root="$experiment_root/runtime"
elan_home="$runtime_root/elan"
lean_project="$experiment_root/lean_project"
runner="$experiment_root/scripts/run_with_heartbeat.sh"
log_root="$experiment_root/runs/preflight/lean-bootstrap"
state_root="$log_root/state"

mkdir -p "$runtime_root" "$log_root" "$state_root"
rm -f "$state_root/FAILED"
printf 'started_at=%s\n' "$(date -Is)" > "$state_root/RUNNING"

on_error() {
  rc=$?
  printf 'failed_at=%s\nexit_code=%s\n' "$(date -Is)" "$rc" > "$state_root/FAILED"
  exit "$rc"
}
trap on_error ERR

export ELAN_HOME="$elan_home"
export PATH="$ELAN_HOME/bin:$PATH"
export HTTPS_PROXY=${HTTPS_PROXY:-http://127.0.0.1:7890}
export HTTP_PROXY=${HTTP_PROXY:-http://127.0.0.1:7890}

if [[ ! -x "$ELAN_HOME/bin/elan" ]]; then
  curl --fail --location --retry 5 --retry-delay 2 \
    https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    -o "$runtime_root/elan-init.sh"
  "$runner" elan-install 900 "$log_root/elan-install.log" -- \
    sh "$runtime_root/elan-init.sh" -y --no-modify-path --default-toolchain none
fi

"$runner" lean-toolchain 1800 "$log_root/lean-toolchain.log" -- \
  "$ELAN_HOME/bin/elan" toolchain install leanprover/lean4:v4.28.0
"$ELAN_HOME/bin/elan" default leanprover/lean4:v4.28.0

"$runner" lake-update 1800 "$log_root/lake-update.log" -- \
  bash -lc "export ELAN_HOME='$ELAN_HOME'; export PATH='$ELAN_HOME/bin':\"\$PATH\"; export HTTPS_PROXY='$HTTPS_PROXY'; export HTTP_PROXY='$HTTP_PROXY'; cd '$lean_project'; lake update"

"$runner" mathlib-cache 3600 "$log_root/mathlib-cache.log" -- \
  bash -lc "export ELAN_HOME='$ELAN_HOME'; export PATH='$ELAN_HOME/bin':\"\$PATH\"; export HTTPS_PROXY='$HTTPS_PROXY'; export HTTP_PROXY='$HTTP_PROXY'; cd '$lean_project'; lake exe cache get"

"$runner" lean-family-smoke 1800 "$log_root/lean-family-smoke.log" -- \
  bash -lc "export ELAN_HOME='$ELAN_HOME'; export PATH='$ELAN_HOME/bin':\"\$PATH\"; cd '$lean_project'; lake env lean families/P01_FATE_M_003.lean"

{
  printf 'completed_at=%s\n' "$(date -Is)"
  "$ELAN_HOME/bin/lean" --version
  "$ELAN_HOME/bin/lake" --version
  sha256sum "$lean_project/lean-toolchain" "$lean_project/lakefile.toml"
} > "$state_root/DONE"
rm -f "$state_root/RUNNING" "$state_root/FAILED"
trap - ERR
cat "$state_root/DONE"

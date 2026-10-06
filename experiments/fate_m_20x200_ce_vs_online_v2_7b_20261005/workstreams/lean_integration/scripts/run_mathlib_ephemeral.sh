#!/usr/bin/env bash
set -Eeuo pipefail

# Finish the pinned Reap + Mathlib runtime on ModelScope's ephemeral /tmp disk.
# The guard aborts before the experiment can consume more than 25 GiB in /tmp.
experiment_dir="${EXPERIMENT_DIR:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}"
workstream_dir="$experiment_dir/workstreams/lean_integration"
ephemeral_dir="${EPHEMERAL_DIR:-/tmp/fate-m-reap428}"
state_dir="${STATE_DIR:-$experiment_dir/runs/preflight/mathlib-ephemeral}"
elan_home="${ELAN_HOME:-$experiment_dir/runtime/elan}"
toolchain="${ELAN_TOOLCHAIN:-fate-lean-4.28.0}"
proxy="${HTTPS_PROXY:-http://127.0.0.1:7890}"
min_start_free_kib="${MIN_TMP_FREE_KIB:-20971520}" # 20 GiB
max_tree_kib="${MAX_EPHEMERAL_TREE_KIB:-26214400}" # 25 GiB
min_runtime_free_kib="${MIN_RUNTIME_FREE_KIB:-3145728}" # 3 GiB

mkdir -p "$state_dir" "$ephemeral_dir"
rm -f "$state_dir/DONE" "$state_dir/FAILED"
started_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
free_before_kib="$(df -Pk "$ephemeral_dir" | awk 'NR==2 {print $4}')"

write_receipt() {
  local status=$1 rc=$2 reason=${3:-}
  local ended_utc free_after_kib tree_kib runtime_receipt_sha=""
  ended_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  free_after_kib="$(df -Pk "$ephemeral_dir" | awk 'NR==2 {print $4}')"
  tree_kib="$(du -sk "$ephemeral_dir" | awk '{print $1}')"
  if [[ -f "$ephemeral_dir/runtime_receipt.json" ]]; then
    runtime_receipt_sha="$(sha256sum "$ephemeral_dir/runtime_receipt.json" | awk '{print $1}')"
  fi
  python3 - "$state_dir/receipt.json" "$status" "$rc" "$reason" "$started_utc" "$ended_utc" \
    "$ephemeral_dir" "$free_before_kib" "$free_after_kib" "$tree_kib" "$toolchain" "$runtime_receipt_sha" <<'PY'
import json, os, sys
out, status, rc, reason, started, ended, root, free_before, free_after, tree_kib, toolchain, runtime_sha = sys.argv[1:]
payload = {
    "schema": "fate-m.mathlib-ephemeral-preflight.v1",
    "status": status,
    "exit_code": int(rc),
    "reason": reason or None,
    "started_utc": started,
    "ended_utc": ended,
    "ephemeral_dir": root,
    "tmp_free_kib_before": int(free_before),
    "tmp_free_kib_after": int(free_after),
    "ephemeral_tree_kib": int(tree_kib),
    "elan_toolchain": toolchain,
    "runtime_receipt_sha256": runtime_sha or None,
}
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
os.replace(tmp, out)
PY
  printf '%s\n' "$status exit_code=$rc ended_utc=$ended_utc reason=$reason" > "$state_dir/$status"
}

if (( free_before_kib < min_start_free_kib )); then
  write_receipt FAILED 30 "less_than_20GiB_free_before_start"
  exit 30
fi
test -x "$elan_home/bin/lean"
test -x "$elan_home/bin/lake"
test -d "$ephemeral_dir/reap/.git"
test "$(git -C "$ephemeral_dir/reap" rev-parse HEAD)" = "0090d73c5f739e4d74000e053b00fd0148ff46aa"
if [[ -e "$ephemeral_dir/runtime_receipt.json" ]]; then
  write_receipt FAILED 31 "runtime_receipt_already_exists_refusing_overwrite"
  exit 31
fi

export ELAN_HOME="$elan_home"
export ELAN_TOOLCHAIN="$toolchain"
export PATH="$elan_home/bin:$PATH"
export HTTPS_PROXY="$proxy"
export HTTP_PROXY="${HTTP_PROXY:-$proxy}"
export GIT_TERMINAL_PROMPT=0
export ALLOW_LARGE_DOWNLOAD=1

printf 'mathlib-start ephemeral_dir=%s free_before_kib=%s max_tree_kib=%s proxy=%s\n' \
  "$ephemeral_dir" "$free_before_kib" "$max_tree_kib" "$proxy"

set +e
setsid "$workstream_dir/scripts/prepare_runtime.sh" "$ephemeral_dir" &
child_pid=$!
capacity_abort=0
while kill -0 "$child_pid" 2>/dev/null; do
  sleep 20
  tree_kib="$(du -sk "$ephemeral_dir" | awk '{print $1}')"
  free_kib="$(df -Pk "$ephemeral_dir" | awk 'NR==2 {print $4}')"
  printf '[storage-heartbeat] ephemeral_tree_kib=%s tmp_free_kib=%s limits=%s/%s\n' \
    "$tree_kib" "$free_kib" "$max_tree_kib" "$min_runtime_free_kib"
  if (( tree_kib > max_tree_kib || free_kib < min_runtime_free_kib )); then
    capacity_abort=1
    kill -TERM -- "-$child_pid" 2>/dev/null || true
    break
  fi
done
wait "$child_pid"
rc=$?
set -e

if (( capacity_abort == 1 )); then
  write_receipt FAILED 32 "ephemeral_capacity_guard_triggered"
  exit 32
fi
if (( rc != 0 )); then
  write_receipt FAILED "$rc" "prepare_runtime_failed"
  exit "$rc"
fi

test -s "$ephemeral_dir/runtime_receipt.json"
test -s "$ephemeral_dir/runtime/.lake/build/lib/lean/ReapRuntime.olean"
lake -d "$ephemeral_dir/runtime" env lean --version
lake -d "$ephemeral_dir/runtime" build ReapRuntime
lake -d "$ephemeral_dir/runtime" env lean "$ephemeral_dir/runtime/ReapRuntime.lean"
cp "$ephemeral_dir/runtime_receipt.json" "$state_dir/runtime_receipt.json"
sha256sum "$state_dir/runtime_receipt.json" > "$state_dir/runtime_receipt.json.sha256"
write_receipt DONE 0 ""
printf 'mathlib ephemeral preflight DONE\n'

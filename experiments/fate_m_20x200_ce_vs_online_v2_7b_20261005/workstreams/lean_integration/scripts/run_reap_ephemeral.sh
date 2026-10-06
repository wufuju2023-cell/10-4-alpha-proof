#!/usr/bin/env bash
set -Eeuo pipefail

# Build the pinned Reap training endpoint on ModelScope's ephemeral /tmp disk.
# Only small, reproducibility-relevant receipts are written to persistent storage.
experiment_dir="${EXPERIMENT_DIR:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}"
workstream_dir="$experiment_dir/workstreams/lean_integration"
ephemeral_dir="${EPHEMERAL_DIR:-/tmp/fate-m-reap428}"
state_dir="${STATE_DIR:-$experiment_dir/runs/preflight/reap-ephemeral}"
elan_home="${ELAN_HOME:-$experiment_dir/runtime/elan}"
toolchain="${ELAN_TOOLCHAIN:-fate-lean-4.28.0}"
proxy="${HTTPS_PROXY:-http://127.0.0.1:7890}"
min_free_kib="${MIN_TMP_FREE_KIB:-12582912}" # 12 GiB safety floor before start.

mkdir -p "$state_dir"
rm -f "$state_dir/DONE" "$state_dir/FAILED"
started_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$ephemeral_dir"
tmp_free_kib="$(df -Pk "$ephemeral_dir" | awk 'NR==2 {print $4}')"

write_receipt() {
  local status=$1
  local rc=$2
  local ended_utc
  ended_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local reap_head=""
  local reap_size="0"
  local manifest_sha=""
  local training_olean_sha=""
  local patched_diff_sha=""
  if [[ -d "$ephemeral_dir/reap/.git" ]]; then
    reap_head="$(git -C "$ephemeral_dir/reap" rev-parse HEAD 2>/dev/null || true)"
    reap_size="$(du -sk "$ephemeral_dir/reap" 2>/dev/null | awk '{print $1}' || true)"
    if [[ -f "$ephemeral_dir/reap/lake-manifest.json" ]]; then
      manifest_sha="$(sha256sum "$ephemeral_dir/reap/lake-manifest.json" | awk '{print $1}')"
    fi
    if [[ -f "$ephemeral_dir/reap/.lake/build/lib/lean/Reap/Training.olean" ]]; then
      training_olean_sha="$(sha256sum "$ephemeral_dir/reap/.lake/build/lib/lean/Reap/Training.olean" | awk '{print $1}')"
    fi
    patched_diff_sha="$(git -C "$ephemeral_dir/reap" diff --binary | sha256sum | awk '{print $1}')"
  fi
  python3 - "$state_dir/receipt.json" "$status" "$rc" "$started_utc" "$ended_utc" \
    "$ephemeral_dir" "$tmp_free_kib" "$reap_head" "$reap_size" "$toolchain" \
    "$manifest_sha" "$training_olean_sha" "$patched_diff_sha" <<'PY'
import json, os, sys
out, status, rc, started, ended, ephemeral, free_kib, head, size_kib, toolchain, manifest_sha, olean_sha, diff_sha = sys.argv[1:]
payload = {
    "schema": "fate-m.reap-ephemeral-preflight.v1",
    "status": status,
    "exit_code": int(rc),
    "started_utc": started,
    "ended_utc": ended,
    "ephemeral_dir": ephemeral,
    "tmp_free_kib_before": int(free_kib),
    "reap_commit": head or None,
    "reap_size_kib": int(size_kib or 0),
    "reap_lake_manifest_sha256": manifest_sha or None,
    "patched_git_diff_sha256": diff_sha or None,
    "training_olean_sha256": olean_sha or None,
    "elan_toolchain": toolchain,
    "persistent_outputs": [os.path.dirname(out), out],
}
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
os.replace(tmp, out)
PY
  printf '%s\n' "$status exit_code=$rc ended_utc=$ended_utc" > "$state_dir/$status"
}

on_exit() {
  local rc=$?
  if [[ ! -f "$state_dir/DONE" ]]; then
    write_receipt FAILED "$rc"
  fi
  exit "$rc"
}
trap on_exit EXIT

if (( tmp_free_kib < min_free_kib )); then
  printf 'insufficient /tmp space: free_kib=%s required_kib=%s\n' "$tmp_free_kib" "$min_free_kib" >&2
  exit 30
fi
test -x "$elan_home/bin/lean"
test -x "$elan_home/bin/lake"

export ELAN_HOME="$elan_home"
export ELAN_TOOLCHAIN="$toolchain"
export PATH="$elan_home/bin:$PATH"
export HTTPS_PROXY="$proxy"
export HTTP_PROXY="${HTTP_PROXY:-$proxy}"
export GIT_TERMINAL_PROMPT=0

lean --version
lake --version
printf 'ephemeral_dir=%s tmp_free_kib_before=%s proxy=%s\n' "$ephemeral_dir" "$tmp_free_kib" "$proxy"
"$workstream_dir/scripts/prepare_runtime.sh" "$ephemeral_dir" --reap-only

test "$(git -C "$ephemeral_dir/reap" rev-parse HEAD)" = "0090d73c5f739e4d74000e053b00fd0148ff46aa"
test -e "$ephemeral_dir/reap/.lake/build/lib/lean/Reap/Training.olean"
write_receipt DONE 0
trap - EXIT
printf 'reap ephemeral preflight DONE\n'

#!/usr/bin/env bash
set -Eeuo pipefail

experiment_root=${1:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
output="$experiment_root/runtime/uploads/lean-4.28.0-linux.tar.zst"
partial="$output.part"
run_dir="$experiment_root/runs/preflight/lean-archive-download"
url=https://releases.lean-lang.org/lean4/v4.28.0/lean-4.28.0-linux.tar.zst
expected_sha=ceb3a3f844f7aebf63245e2b51c28d5b0ed38942c19f93cf3febd520302160bd
proxy=${HTTPS_PROXY:-http://127.0.0.1:7890}

mkdir -p "$(dirname "$output")" "$run_dir"
rm -f "$run_dir/DONE" "$run_dir/FAILED"

# Resuming through the releases.lean-lang.org redirect chain can append a
# redirect response before the ranged GitHub asset.  Never trust or extend an
# unverified prefix: quarantine it, download to a fresh .part, then atomically
# publish only after both size and SHA-256 match.
quarantined=
if [[ -f "$output" ]]; then
  current_size=$(stat -c %s "$output")
  current_sha=$(sha256sum "$output" | cut -d' ' -f1)
  if [[ "$current_size" == "520948633" && "$current_sha" == "$expected_sha" ]]; then
    printf 'status=success\ncompleted_at=%s\nbytes=%s\nsha256=%s\nsource=already_verified\n' \
      "$(date -Is)" "$current_size" "$current_sha" > "$run_dir/DONE"
    cat "$run_dir/DONE"
    exit 0
  fi
  quarantined="$run_dir/corrupt-prefix-${current_size}.tar.zst"
  mv -- "$output" "$quarantined"
  printf 'quarantined=%s\nbytes=%s\nsha256=%s\n' \
    "$quarantined" "$current_size" "$current_sha" > "$run_dir/quarantine.txt"
fi

# A previous invocation may have finished the bytes and failed only during
# publication (for example, a receipt/check typo). Recover that exact state
# without downloading again.
if [[ -f "$partial" ]]; then
  partial_size=$(stat -c %s "$partial")
  partial_sha=$(sha256sum "$partial" | cut -d' ' -f1)
  if [[ "$partial_size" == "520948633" && "$partial_sha" == "$expected_sha" ]]; then
    mv -- "$partial" "$output"
    if [[ -n "$quarantined" ]]; then
      rm -f -- "$quarantined"
    fi
    printf 'status=success\ncompleted_at=%s\nbytes=%s\nsha256=%s\nsource=recovered_verified_partial\n' \
      "$(date -Is)" "$partial_size" "$partial_sha" > "$run_dir/DONE"
    rm -f "$run_dir/FAILED"
    cat "$run_dir/DONE"
    exit 0
  fi
fi
rm -f -- "$partial"

on_error() {
  rc=$?
  printf 'status=failed\nfailed_at=%s\nexit_code=%s\npartial_bytes=%s\n' \
    "$(date -Is)" "$rc" "$(stat -c %s "$partial" 2>/dev/null || echo 0)" \
    > "$run_dir/FAILED"
  exit "$rc"
}
trap on_error ERR

start=$(date +%s)
curl --proxy "$proxy" --fail --location \
  --retry 20 --retry-all-errors --retry-delay 3 \
  --speed-limit 1 --speed-time 600 --progress-bar \
  -o "$partial" "$url" &
curl_pid=$!

while kill -0 "$curl_pid" 2>/dev/null; do
  now=$(date +%s)
  bytes=$(stat -c %s "$partial" 2>/dev/null || echo 0)
  printf '[heartbeat] stage=lean-archive-download elapsed_seconds=%s bytes=%s expected_bytes=520948633\n' \
    "$((now - start))" "$bytes"
  sleep 20
done
wait "$curl_pid"

[[ "$(stat -c %s "$partial")" == "520948633" ]]
actual_sha=$(sha256sum "$partial" | cut -d' ' -f1)
[[ "$actual_sha" == "$expected_sha" ]]
mv -- "$partial" "$output"
if [[ -n "$quarantined" ]]; then
  rm -f -- "$quarantined"
fi
printf 'status=success\ncompleted_at=%s\nbytes=%s\nsha256=%s\n' \
  "$(date -Is)" "$(stat -c %s "$output")" "$actual_sha" > "$run_dir/DONE"
rm -f "$run_dir/FAILED"
trap - ERR
cat "$run_dir/DONE"

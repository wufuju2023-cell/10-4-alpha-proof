#!/usr/bin/env bash
set -euo pipefail

# Builds only under the explicit destination. Mathlib cache/build is potentially
# 8–12 GiB and is therefore gated by ALLOW_LARGE_DOWNLOAD=1.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workstream_dir="$(cd "$script_dir/.." && pwd)"
experiment_root="${FATE_EXPERIMENT_ROOT:-$(cd "$workstream_dir/../.." && pwd)}"
destination="${1:?usage: prepare_runtime.sh DESTINATION [--reap-only]}"
mode="${2:-}"
reap_commit="0090d73c5f739e4d74000e053b00fd0148ff46aa"

# shellcheck source=scripts/lib/resolve_lean_tools.sh
source "$script_dir/lib/resolve_lean_tools.sh"
lake_bin="$(fate_resolve_lake_bin "$experiment_root")"
lake_bin_dir="$(dirname "$lake_bin")"
# Dependency subprocesses may invoke `lean` by name, but the selected toolchain
# always wins. Resolution of lake itself never falls back to PATH implicitly.
export PATH="$lake_bin_dir:$PATH"
if [[ -n "${LAKE_BIN:-}" ]]; then
  lake_source=LAKE_BIN
elif [[ -n "${LEAN_BIN_DIR:-}" ]]; then
  lake_source=LEAN_BIN_DIR
else
  lake_source=experiment-runtime
fi
printf 'resolved_lake=%s source=%s\n' "$lake_bin" "$lake_source"

mkdir -p "$destination"
destination="$(cd "$destination" && pwd)"
reap_dir="$destination/reap"
runtime_dir="$destination/runtime"

if [[ ! -d "$reap_dir/.git" ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/IQuestLab/reap.git "$reap_dir"
  git -C "$reap_dir" fetch --depth 1 origin "$reap_commit"
  git -C "$reap_dir" checkout --detach FETCH_HEAD
fi
test "$(git -C "$reap_dir" rev-parse HEAD)" = "$reap_commit"

# Do not run `lake update` in Reap: its lakefile says `main`, while the checked-in
# manifest pins the dependency revisions used by this exact commit.
cp "$workstream_dir/runtime/overlay/Reap/Training.lean" "$reap_dir/Reap/Training.lean"
mkdir -p "$reap_dir/Reap/Training"
cp "$workstream_dir/runtime/overlay/Reap/Training/"*.lean "$reap_dir/Reap/Training/"
python3 -c 'from pathlib import Path; import sys; p = Path(sys.argv[1]); p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))' \
  "$reap_dir/Reap/Training/RolloutSink.lean"
for patch in "$workstream_dir/runtime/patches/0001-training-endpoints-and-value.patch" \
             "$workstream_dir/runtime/patches/0002-training-observer.patch" \
             "$workstream_dir/runtime/patches/0003-strict-value-errors.patch" \
             "$workstream_dir/runtime/patches/0004-canonical-candidate-provenance.patch" \
             "$workstream_dir/runtime/patches/0005-canonical-selected-path-producer.patch"; do
  if git -C "$reap_dir" apply --reverse --check "$patch" >/dev/null 2>&1; then
    continue
  fi
  git -C "$reap_dir" apply --check "$patch"
  git -C "$reap_dir" apply "$patch"
done
printf '%s\n' 'leanprover/lean4:v4.28.0' > "$reap_dir/lean-toolchain"

git -C "$reap_dir" diff --check
(cd "$reap_dir" && "$lake_bin" build Reap.Training)
printf '%s\n' '{"stage":"reap","status":"DONE","lean":"v4.28.0","reap_commit":"0090d73c5f739e4d74000e053b00fd0148ff46aa"}'

if [[ "$mode" = "--reap-only" ]]; then
  exit 0
fi
if [[ "${ALLOW_LARGE_DOWNLOAD:-0}" != "1" ]]; then
  printf '%s\n' 'Refusing the 8–12 GiB Mathlib stage. Recheck ModelScope UI storage, then set ALLOW_LARGE_DOWNLOAD=1.' >&2
  exit 20
fi

mkdir -p "$runtime_dir"
cp "$workstream_dir/runtime/lean-toolchain" "$runtime_dir/lean-toolchain"
cp "$workstream_dir/runtime/lakefile.toml" "$runtime_dir/lakefile.toml"
cp "$workstream_dir/runtime/ReapRuntime.lean" "$runtime_dir/ReapRuntime.lean"
(cd "$runtime_dir" && "$lake_bin" update && "$lake_bin" exe cache get && "$lake_bin" build)
PYTHONPATH="$workstream_dir/src" python3 -m fate_reap.runtime_provenance \
  --reap-dir "$reap_dir" --runtime-dir "$runtime_dir" \
  --patch-dir "$workstream_dir/runtime/patches" \
  --expected-reap-commit "$reap_commit" \
  --lake-bin "$lake_bin" \
  --output "$destination/runtime_receipt.json"
printf '%s\n' '{"stage":"runtime","status":"DONE","lean":"v4.28.0","mathlib":"v4.28.0"}'

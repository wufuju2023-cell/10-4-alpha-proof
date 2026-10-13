#!/usr/bin/env bash
set -euo pipefail

workstream_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/resolve_lean_tools.sh
source "$workstream_dir/scripts/lib/resolve_lean_tools.sh"
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT

make_lake() {
  mkdir -p "$(dirname "$1")"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$1"
  chmod 700 "$1"
}

auto="$root/runtime/toolchains/lean-4.28.0-linux/bin/lake"
dir_override="$root/dir-override/lake"
explicit="$root/explicit/lake"
make_lake "$auto"
make_lake "$dir_override"
make_lake "$explicit"

unset LAKE_BIN LEAN_BIN_DIR
[[ "$(fate_resolve_lake_bin "$root")" = "$auto" ]]

LEAN_BIN_DIR="$(dirname "$dir_override")"
export LEAN_BIN_DIR
[[ "$(fate_resolve_lake_bin "$root")" = "$dir_override" ]]

LAKE_BIN="$explicit"
export LAKE_BIN
[[ "$(fate_resolve_lake_bin "$root")" = "$explicit" ]]

rm -f "$explicit"
if fate_resolve_lake_bin "$root" >"$root/out" 2>"$root/err"; then
  printf 'missing explicit LAKE_BIN unexpectedly succeeded\n' >&2
  exit 1
fi
grep -q 'lake executable unavailable' "$root/err"

# Removing all candidates must not fall back to a lake command on PATH.
unset LAKE_BIN LEAN_BIN_DIR
rm -f "$auto" "$dir_override"
if PATH="$(dirname "$(command -v bash)"):$PATH" fate_resolve_lake_bin "$root" >"$root/out" 2>"$root/err"; then
  printf 'implicit PATH fallback unexpectedly succeeded\n' >&2
  exit 1
fi
grep -q 'source=experiment-runtime' "$root/err"

printf 'resolve-lean-tools: PASS\n'

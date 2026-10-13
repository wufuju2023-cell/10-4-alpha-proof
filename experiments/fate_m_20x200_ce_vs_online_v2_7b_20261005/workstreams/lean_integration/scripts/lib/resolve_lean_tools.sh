#!/usr/bin/env bash

# Resolve the Lean 4.28 `lake` executable without an implicit PATH fallback.
# Priority: LAKE_BIN, LEAN_BIN_DIR/lake, then the experiment-owned toolchain.
fate_resolve_lake_bin() {
  local experiment_root=${1:?experiment root is required}
  local candidate source
  if [[ -n "${LAKE_BIN:-}" ]]; then
    candidate=$LAKE_BIN
    source=LAKE_BIN
  elif [[ -n "${LEAN_BIN_DIR:-}" ]]; then
    candidate=${LEAN_BIN_DIR%/}/lake
    source=LEAN_BIN_DIR
  else
    candidate=${experiment_root%/}/runtime/toolchains/lean-4.28.0-linux/bin/lake
    source=experiment-runtime
  fi

  # A command name without a slash is allowed only when the caller explicitly
  # supplied LAKE_BIN. There is deliberately no implicit `command -v lake`.
  if [[ "$candidate" != */* ]]; then
    if [[ "$source" != LAKE_BIN ]]; then
      printf 'invalid lake candidate without path from %s: %s\n' "$source" "$candidate" >&2
      return 127
    fi
    candidate=$(command -v -- "$candidate" 2>/dev/null || true)
  fi
  if [[ -z "$candidate" || ! -f "$candidate" || ! -x "$candidate" ]]; then
    printf 'lake executable unavailable (source=%s candidate=%s). Set LAKE_BIN or LEAN_BIN_DIR, or install %s/runtime/toolchains/lean-4.28.0-linux/bin/lake\n' \
      "$source" "${candidate:-<not-found>}" "$experiment_root" >&2
    return 127
  fi
  local directory base
  directory=$(cd "$(dirname "$candidate")" && pwd -P)
  base=$(basename "$candidate")
  printf '%s/%s\n' "$directory" "$base"
}

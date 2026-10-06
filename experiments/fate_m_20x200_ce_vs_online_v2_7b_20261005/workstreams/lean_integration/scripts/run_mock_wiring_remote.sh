#!/usr/bin/env bash
set -euo pipefail

# Diagnostic only: exercise the pinned Reap/Lean observer and raw-tree output
# with a deterministic mock response.  All bulky/transient output stays on
# /tmp; the caller may later copy only the small evidence files.
experiment_root=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
reap_project=/tmp/fate-m-reap428/runtime
run_root=${FATE_MOCK_WIRING_ROOT:-/tmp/fate-m-mock-wiring}
problems="$experiment_root/data/problems.jsonl"
mock_server="$experiment_root/workstreams/lean_integration/scripts/mock_reap_openai.py"
lean_src="$experiment_root/workstreams/lean_integration/src"
lake_bin="$experiment_root/runtime/toolchains/lean-4.28.0-linux/bin/lake"

test -f "$reap_project/.lake/build/lib/lean/ReapRuntime.olean"
test -f "$problems"
test -f "$mock_server"
test -x "$lake_bin"

if [[ -e "$run_root" ]]; then
  echo "refusing to overwrite diagnostic root: $run_root" >&2
  exit 2
fi
mkdir -p "$run_root/theorems" "$run_root/session"

python3 "$mock_server" --port 18080 >"$run_root/mock-server.jsonl" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 50); do
  if python3 - <<'PY'
import urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:18080/health", timeout=1).read()
except Exception:
    raise SystemExit(1)
PY
  then break; fi
  sleep 0.1
done

problems_sha256=$(sha256sum "$problems" | cut -d' ' -f1)
PYTHONPATH="$lean_src" python3 -m fate_reap.session_builder \
  --problems "$problems" --expected-problems-sha256 "$problems_sha256" \
  --model-sha256 0000000000000000000000000000000000000000000000000000000000000000 \
  --output-dir "$run_root/theorems" --manifest "$run_root/sessions.jsonl" \
  --policy-base-url http://127.0.0.1:18080/v1 \
  --value-base-url http://127.0.0.1:18080/v1 \
  --variant-start 1 --variant-end 1 --families 3 \
  --num-samples 64 --max-tokens 256 --max-steps 1 --max-goals 8

theorem="$run_root/theorems/P03/v001.lean"
test -f "$theorem"

export REAP_SESSION_ID=fate_m_003_v001
export REAP_SESSION_DIR="$run_root/session"
export REAP_POLICY_ENDPOINT=http://127.0.0.1:18080/v1
export REAP_VALUE_ENDPOINT=http://127.0.0.1:18080/v1
export REAP_OBSERVER_PATH="$run_root/session/observer.jsonl"
export REAP_TREE_ID=fate_m_003_v001-tree
export REAP_POLICY_VERSION=0

started=$(date -Is)
set +e
(cd "$reap_project" && "$lake_bin" env lean "$theorem") \
  >"$run_root/lean.stdout.log" 2>"$run_root/lean.stderr.log"
lean_rc=$?
set -e
ended=$(date -Is)

python3 - "$run_root" "$started" "$ended" "$lean_rc" <<'PY'
from pathlib import Path
import hashlib, json, sys

root = Path(sys.argv[1])
files = {}
for path in sorted(root.rglob("*")):
    if path.is_file():
        files[str(path.relative_to(root))] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
report = {
    "schema_version": "fate.mock_reap_wiring.v1",
    "scope": "diagnostic_only_not_model_evidence",
    "started_at": sys.argv[2],
    "ended_at": sys.argv[3],
    "lean_returncode": int(sys.argv[4]),
    "files": files,
}
(root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(json.dumps(report, sort_keys=True))
PY

if [[ "$lean_rc" -ne 0 ]]; then
  exit "$lean_rc"
fi

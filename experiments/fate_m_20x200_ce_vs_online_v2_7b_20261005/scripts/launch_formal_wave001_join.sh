#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1

EXP=${FATE_EXPERIMENT_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
MODEL=${FATE_MODEL_ROOT:-/mnt/workspace/models/REAL-Prover-fe76f68d}
WORKSPACE_ROOT=${FATE_WORKSPACE_ROOT:-/mnt/workspace}
ACTOR_ROOT=${ACTOR_ROOT:-/tmp/fate-m-formal-w001-shared-rollout-v2}
JOIN_ROOT=${JOIN_ROOT:-/tmp/fate-m-formal-w001-join-v1}
PROBLEMS=${FATE_PROBLEMS_PATH:-}
if [[ -z "$PROBLEMS" ]]; then
  PROBLEMS=$(python3 "$EXP/scripts/materialize_problems_jsonl.py")
fi

test -f "$ACTOR_ROOT/DONE.json"
mapfile -t BOOTSTRAPS < <(find "$EXP/runs/formal" -mindepth 2 -maxdepth 2 -name bootstrap.json -type f | sort)
if [[ ${#BOOTSTRAPS[@]} -ne 1 ]]; then
  printf 'expected exactly one verifier bootstrap, found %s\n' "${#BOOTSTRAPS[@]}" >&2
  exit 2
fi
BOOTSTRAP_JSON="${BOOTSTRAPS[0]}"

eval "$(BOOTSTRAP_JSON="$BOOTSTRAP_JSON" python3 - <<'PY'
import json, os, shlex
from pathlib import Path
d = json.loads(Path(os.environ['BOOTSTRAP_JSON']).read_text(encoding='utf-8'))
for key, field in [
    ('BUDGET_CONFIG', 'budget_config'),
    ('VERIFIER_LOCK', 'verifier_lock'),
    ('PRIVATE_KEY', 'private_key_path'),
    ('COURSE_PROJECT', 'course_project'),
    ('RUNTIME_RECEIPT', 'runtime_receipt'),
    ('LAKE', 'lake'),
]:
    print(f'{key}={shlex.quote(d[field])}')
PY
)"

for p in "$BUDGET_CONFIG" "$VERIFIER_LOCK" "$PRIVATE_KEY" "$RUNTIME_RECEIPT" "$LAKE"; do
  test -f "$p"
done
test ! -e "$JOIN_ROOT"

python3 "$EXP/workstreams/policy_service_bridge/scripts/run_wave_join_adapter.py" \
  --actor-root "$ACTOR_ROOT" \
  --plan "$EXP/workstreams/policy_service_bridge/config/wave001_20family_plan.frozen.json" \
  --wave-index 1 \
  --tokenizer-dir "$MODEL" \
  --budget-config "$BUDGET_CONFIG" \
  --runtime-receipt "$RUNTIME_RECEIPT" \
  --course-project "$COURSE_PROJECT" \
  --course-manifest "$COURSE_PROJECT/lake-manifest.json" \
  --lake "$LAKE" \
  --verifier-lock "$VERIFIER_LOCK" \
  --private-key "$PRIVATE_KEY" \
  --workspace-root "$WORKSPACE_ROOT" \
  --problems "$PROBLEMS" \
  --output-root "$JOIN_ROOT"

test -f "$JOIN_ROOT/DONE.json"
test -f "$JOIN_ROOT/training-inputs/manifest.json"
echo "FORMAL_WAVE001_JOIN_DONE=$JOIN_ROOT"

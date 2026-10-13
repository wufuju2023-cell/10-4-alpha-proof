#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 7 ]; then
  echo "usage: $0 WAVE_INDEX CE_ADAPTER ONLINE_ADAPTER PLAN ROOT_STATE_PINS PAIR_ROOT BOOTSTRAP_JSON" >&2
  exit 2
fi

WAVE_INDEX="$1"
CE_ADAPTER="$2"
ONLINE_ADAPTER="$3"
PLAN="$4"
ROOT_STATE_PINS="$5"
PAIR_ROOT="$6"
BOOTSTRAP_JSON="$7"

EXP="${EXP:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}"
MODEL="${MODEL:-/mnt/workspace/models/REAL-Prover-fe76f68d}"
PROBLEMS="${PROBLEMS:-}"
if [[ -z "$PROBLEMS" ]]; then
  PROBLEMS=$(python3 "$EXP/scripts/materialize_problems_jsonl.py")
fi
REAP_PROJECT="${REAP_PROJECT:-/tmp/fate-m-reap428/runtime}"
PROMPT_BUILDER="${PROMPT_BUILDER:-$EXP/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py}"
ACTOR_TEMPLATE="${ACTOR_TEMPLATE:-$EXP/workstreams/ce_arm/config/shared_actor.wave001.fixed.json}"

json_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' "$1" "$2"
}

BUDGET_CONFIG="$(json_field "$BOOTSTRAP_JSON" budget_config)"
VERIFIER_LOCK="$(json_field "$BOOTSTRAP_JSON" verifier_lock)"
PRIVATE_KEY="$(json_field "$BOOTSTRAP_JSON" private_key_path)"
COURSE_PROJECT="$(json_field "$BOOTSTRAP_JSON" course_project)"
COURSE_MANIFEST="$(json_field "$BOOTSTRAP_JSON" course_manifest)"
RUNTIME_RECEIPT="$(json_field "$BOOTSTRAP_JSON" runtime_receipt)"
LAKE="$(json_field "$BOOTSTRAP_JSON" lake)"

case "$WAVE_INDEX" in
  ''|*[!0-9]*) echo "WAVE_INDEX must be a positive integer" >&2; exit 2 ;;
esac
if [ "$WAVE_INDEX" -lt 1 ] || [ "$WAVE_INDEX" -gt 8 ]; then
  echo "formal train WAVE_INDEX must be in 1..8" >&2
  exit 2
fi
for path in "$PLAN" "$ROOT_STATE_PINS" "$BOOTSTRAP_JSON" "$PROBLEMS" \
  "$PROMPT_BUILDER" "$ACTOR_TEMPLATE" "$BUDGET_CONFIG" "$VERIFIER_LOCK" \
  "$PRIVATE_KEY" "$COURSE_MANIFEST" "$RUNTIME_RECEIPT" "$LAKE"; do
  test -f "$path" || { echo "missing file: $path" >&2; exit 2; }
done
for path in "$MODEL" "$REAP_PROJECT" "$COURSE_PROJECT" "$CE_ADAPTER" "$ONLINE_ADAPTER"; do
  test -d "$path" || { echo "missing directory: $path" >&2; exit 2; }
done
for adapter in "$CE_ADAPTER" "$ONLINE_ADAPTER"; do
  test -s "$adapter/adapter_model.safetensors" || { echo "missing adapter weights: $adapter" >&2; exit 2; }
  test -s "$adapter/adapter_config.json" || { echo "missing adapter config: $adapter" >&2; exit 2; }
done
test ! -e "$PAIR_ROOT" || { echo "immutable PAIR_ROOT already exists: $PAIR_ROOT" >&2; exit 2; }

CE_ROOT="$PAIR_ROOT/ce"
ONLINE_ROOT="$PAIR_ROOT/online_v2"
mkdir -p "$CE_ROOT" "$ONLINE_ROOT"

make_actor_config() {
  local arm="$1"
  local output="$2"
  local alias="${arm}-wave-$(printf '%03d' "$WAVE_INDEX")-input"
  jq --arg alias "$alias" \
    '.policy_version=$alias | .behavior_adapter_alias=$alias | .generation.max_new_tokens=512' \
    "$ACTOR_TEMPLATE" > "$output"
}

make_actor_config ce "$CE_ROOT/actor-config.json"
make_actor_config online-v2 "$ONLINE_ROOT/actor-config.json"

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$EXP/workstreams/policy_service_bridge/src:$EXP/workstreams/shared_actor_bridge/src:$EXP/workstreams/lean_integration/src${PYTHONPATH:+:$PYTHONPATH}"

run_actor() {
  local arm_root="$1"
  local adapter="$2"
  local adapter_sha
  adapter_sha="$(sha256sum "$adapter/adapter_model.safetensors" | awk '{print $1}')"
  python3 "$EXP/workstreams/policy_service_bridge/scripts/real_reap_wave_one_step.py" \
    --model "$MODEL" --adapter "$adapter" \
    --expected-adapter-model-sha256 "$adapter_sha" \
    --problems "$PROBLEMS" --plan "$PLAN" --root-state-pins "$ROOT_STATE_PINS" \
    --wave-index "$WAVE_INDEX" --actor-config "$arm_root/actor-config.json" \
    --prompt-builder "$PROMPT_BUILDER" --reap-project "$REAP_PROJECT" --lake "$LAKE" \
    --output-dir "$arm_root/actor" --per-session-timeout-seconds 900
}

run_join() {
  local arm_root="$1"
  python3 "$EXP/workstreams/policy_service_bridge/scripts/run_wave_join_adapter.py" \
    --actor-root "$arm_root/actor" --plan "$PLAN" --wave-index "$WAVE_INDEX" \
    --tokenizer-dir "$MODEL" --budget-config "$BUDGET_CONFIG" \
    --runtime-receipt "$RUNTIME_RECEIPT" --course-project "$COURSE_PROJECT" \
    --course-manifest "$COURSE_MANIFEST" --lake "$LAKE" \
    --verifier-lock "$VERIFIER_LOCK" --private-key "$PRIVATE_KEY" \
    --workspace-root /mnt/workspace --problems "$PROBLEMS" \
    --output-root "$arm_root/join"
}

ce_join_pid=""
cleanup() {
  local status=$?
  if [ -n "$ce_join_pid" ] && kill -0 "$ce_join_pid" 2>/dev/null; then
    kill "$ce_join_pid" 2>/dev/null || true
    wait "$ce_join_pid" 2>/dev/null || true
  fi
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Keep the GPU occupied: as soon as CE generation releases it, start the
# Online-v2 rollout while CE's CPU-only candidate/join/replay stage runs.
run_actor "$CE_ROOT" "$CE_ADAPTER" 2>&1 | tee "$CE_ROOT/actor.stdout.log"
run_join "$CE_ROOT" > >(tee "$CE_ROOT/join.stdout.log") 2>&1 &
ce_join_pid=$!
run_actor "$ONLINE_ROOT" "$ONLINE_ADAPTER" 2>&1 | tee "$ONLINE_ROOT/actor.stdout.log"
run_join "$ONLINE_ROOT" 2>&1 | tee "$ONLINE_ROOT/join.stdout.log"
wait "$ce_join_pid"
ce_join_pid=""

test "$(json_field "$CE_ROOT/actor/DONE.json" state)" = DONE
test "$(json_field "$ONLINE_ROOT/actor/DONE.json" state)" = DONE
test "$(json_field "$CE_ROOT/join/DONE.json" state)" = DONE
test "$(json_field "$ONLINE_ROOT/join/DONE.json" state)" = DONE
trap - EXIT INT TERM
printf '{"event":"paired_wave_rollout_join_complete","wave_index":%s,"ce_root":"%s","online_v2_root":"%s"}\n' \
  "$WAVE_INDEX" "$CE_ROOT" "$ONLINE_ROOT"

#!/usr/bin/env bash
set -Eeuo pipefail

# Required machine pins. The caller may override paths but never hashes/config.
EXP="${EXP:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}"
REAP_PROJECT="${REAP_PROJECT:-/tmp/fate-m-reap428/runtime}"
LAKE="${LAKE:-$EXP/runtime/toolchains/lean-4.28.0-linux/bin/lake}"
MODEL="${MODEL:-/mnt/workspace/models/REAL-Prover-fe76f68d}"
ADAPTER="${ADAPTER:-$EXP/assets/initial_lora_r16_a32_seed20261004}"
PROMPT_BUILDER="${PROMPT_BUILDER:-$EXP/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py}"
PLAN="$EXP/workstreams/policy_service_bridge/config/wave001_20family_plan.frozen.json"
ACTOR_CONFIG="$EXP/workstreams/ce_arm/config/shared_actor.wave001.fixed.json"
RUN_ROOT="${RUN_ROOT:-/tmp/fate-m-wave001-one-step-$(date -u +%Y%m%dT%H%M%SZ)}"
CAPTURE="$RUN_ROOT/root-state-capture"
ACTOR="$RUN_ROOT/actor"
PROBLEMS="${PROBLEMS:-}"
if [[ -z "$PROBLEMS" ]]; then
  PROBLEMS=$(python3 "$EXP/scripts/materialize_problems_jsonl.py")
fi

for path in "$PROBLEMS" "$PLAN" "$ACTOR_CONFIG" "$LAKE" "$PROMPT_BUILDER"; do
  test -f "$path" || { echo "missing file: $path" >&2; exit 2; }
done
for path in "$REAP_PROJECT" "$MODEL" "$ADAPTER"; do
  test -d "$path" || { echo "missing directory: $path" >&2; exit 2; }
done
test ! -e "$RUN_ROOT" || { echo "immutable RUN_ROOT already exists: $RUN_ROOT" >&2; exit 2; }
mkdir -p "$RUN_ROOT"

export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$EXP/workstreams/policy_service_bridge/src:$EXP/workstreams/shared_actor_bridge/src:$EXP/workstreams/lean_integration/src${PYTHONPATH:+:$PYTHONPATH}"

python3 "$EXP/workstreams/policy_service_bridge/scripts/capture_representative_reap_prompts.py" \
  --problems "$PROBLEMS" --plan "$PLAN" --reap-project "$REAP_PROJECT" \
  --lake "$LAKE" --output-dir "$CAPTURE" --per-session-timeout-seconds 300 \
  --total-timeout-seconds 7200 --heartbeat-seconds 20

python3 "$EXP/workstreams/policy_service_bridge/scripts/real_reap_wave_one_step.py" \
  --model "$MODEL" --adapter "$ADAPTER" --problems "$PROBLEMS" \
  --plan "$PLAN" --root-state-pins "$CAPTURE/root_state_pins.json" \
  --actor-config "$ACTOR_CONFIG" --prompt-builder "$PROMPT_BUILDER" \
  --reap-project "$REAP_PROJECT" --lake "$LAKE" --output-dir "$ACTOR" \
  --per-session-timeout-seconds 900

printf 'ACTOR_WAVE_DONE\n' > "$RUN_ROOT/STATUS"
printf '%s\n' "$RUN_ROOT"

# Signing/conversion is intentionally a separate fail-closed stage.  Run
# workstreams/policy_service_bridge/scripts/run_wave_join_adapter.py on
# "$ACTOR" with the current verifier/runtime/budget/course pins.  The exact
# command is in workstreams/policy_service_bridge/WAVE001_RUNBOOK.md.

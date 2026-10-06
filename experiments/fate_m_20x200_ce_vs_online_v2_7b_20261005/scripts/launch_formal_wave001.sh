#!/usr/bin/env bash
set -euo pipefail

EXP=${FATE_EXPERIMENT_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
OUT=${FATE_ROLLOUT_ROOT:-/tmp/fate-m-formal-w001-shared-rollout-v2}
CFG=${FATE_ACTOR_CONFIG:-/tmp/fate-m-formal-w001-actor-v2.json}
LOG=${FATE_ROLLOUT_LOG:-/tmp/fate-m-formal-w001-rollout-v2.log}
MODEL=${FATE_MODEL_ROOT:-/mnt/workspace/models/REAL-Prover-fe76f68d}
ADAPTER=${FATE_ADAPTER_ROOT:-"$EXP/assets/initial_lora_r16_a32_seed20261004"}
ROOT_STATE_PINS=${FATE_ROOT_STATE_PINS:-"$EXP/runs/preflight/wave001-restart-handoff-20261006/root_state_pins.json"}
PROMPT_BUILDER=${FATE_PROMPT_BUILDER:-"$EXP/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py"}
REAP_PROJECT=${FATE_REAP_PROJECT:-/tmp/fate-m-reap428/runtime}
LAKE=${FATE_REAL_LAKE:-"$EXP/runtime/toolchains/lean-4.28.0-linux/bin/lake"}
PROBLEMS=${FATE_PROBLEMS_PATH:-}
if [[ -z "$PROBLEMS" ]]; then
  PROBLEMS=$(python3 "$EXP/scripts/materialize_problems_jsonl.py")
fi

if [[ -e "$OUT" ]]; then
  echo "refusing_existing_output=$OUT" >&2
  exit 2
fi

python3 - "$EXP/workstreams/ce_arm/config/shared_actor.wave001.fixed.json" "$CFG" <<'PY'
import json
import pathlib
import sys

source, target = map(pathlib.Path, sys.argv[1:])
payload = json.loads(source.read_text(encoding="utf-8"))
payload["policy_version"] = "shared-wave-001-input"
payload["behavior_adapter_alias"] = "shared-wave-001-input"
payload.setdefault("generation", {})["max_new_tokens"] = 512
target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY

adapter_sha="$(sha256sum "$ADAPTER/adapter_model.safetensors" | awk '{print $1}')"
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$EXP/workstreams/policy_service_bridge/src:$EXP/workstreams/shared_actor_bridge/src:$EXP/workstreams/lean_integration/src"

nohup python3 "$EXP/workstreams/policy_service_bridge/scripts/real_reap_wave_one_step.py" \
  --model "$MODEL" \
  --adapter "$ADAPTER" \
  --expected-adapter-model-sha256 "$adapter_sha" \
  --problems "$PROBLEMS" \
  --plan "$EXP/workstreams/policy_service_bridge/config/wave001_20family_plan.frozen.json" \
  --root-state-pins "$ROOT_STATE_PINS" \
  --wave-index 1 \
  --actor-config "$CFG" \
  --prompt-builder "$PROMPT_BUILDER" \
  --reap-project "$REAP_PROJECT" \
  --lake "$LAKE" \
  --output-dir "$OUT" \
  --per-session-timeout-seconds 900 > "$LOG" 2>&1 &

pid=$!
printf '%s\n' "$pid" > /tmp/fate-m-formal-w001-rollout.pid
echo "formal_rollout_pid=$pid"
echo "formal_rollout_log=$LOG"

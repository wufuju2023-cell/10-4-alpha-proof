#!/usr/bin/env bash
set -euo pipefail

EXP=${EXP:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
MODEL=/mnt/workspace/models/REAL-Prover-fe76f68d
MODEL_REVISION=fe76f68d9a88f342cb7b546307c20292fea9cced
VALUE_HEAD=/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt
ADAPTER="$EXP/assets/initial_lora_r16_a32_seed20261004"
TARGET="$EXP/src/10-4-alpha-proof"
BOOTSTRAP="$EXP/runs/formal/verifier-bootstrap-d4f4aee60043-1830ced87793/bootstrap.json"
LOCK_ROOT="$EXP/runs/formal/control/locks"
ACTOR="$EXP/runs/formal/control/shared_actor.wave001.formal512.json"
TOKENIZER_LOCK="$LOCK_ROOT/tokenizer.lock.json"
ASSET_LOCK="$LOCK_ROOT/assets.lock.json"
CE_CONFIG="$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json"

for path in "$MODEL" "$VALUE_HEAD" "$ADAPTER" "$TARGET" "$BOOTSTRAP" \
  /tmp/fate-m-formal-w001-actor-v2.json; do
  test -e "$path" || { echo "missing_required_path=$path" >&2; exit 2; }
done

test "$(git -C "$TARGET" rev-parse HEAD)" = e1b7ffe6feba8cbdb590d42836d750504aa86b94
if test -n "$(git -C "$TARGET" status --porcelain)"; then
  echo "target_repo_not_clean=$TARGET" >&2
  git -C "$TARGET" status --short >&2
  exit 2
fi

VALUE_SOURCE="$TARGET/alphaproof/net/value_head.py"
test -f "$VALUE_SOURCE" || { echo "missing_value_head_source=$VALUE_SOURCE" >&2; exit 2; }
mkdir -p "$LOCK_ROOT" "$(dirname "$ACTOR")"
if test -e "$ACTOR"; then
  cmp -s /tmp/fate-m-formal-w001-actor-v2.json "$ACTOR" || {
    echo "persistent_actor_differs=$ACTOR" >&2; exit 2;
  }
else
  cp /tmp/fate-m-formal-w001-actor-v2.json "$ACTOR"
fi

export PYTHONPATH="$EXP/workstreams/ce_arm/src${PYTHONPATH:+:$PYTHONPATH}"
if ! test -f "$TOKENIZER_LOCK"; then
  echo "phase=generate_tokenizer_lock"
  python3 -m ce_arm.cli generate-tokenizer-lock \
    --model-root "$MODEL" --model-revision "$MODEL_REVISION" \
    --output "$TOKENIZER_LOCK" > "$LOCK_ROOT/tokenizer-lock-generation.json"
fi
if ! test -f "$ASSET_LOCK"; then
  echo "phase=generate_asset_lock"
  python3 -m ce_arm.cli generate-asset-lock \
    --model-root "$MODEL" --model-revision "$MODEL_REVISION" \
    --value-head "$VALUE_HEAD" --initial-adapter "$ADAPTER" \
    --target-repo "$TARGET" --target-value-head-source "$VALUE_SOURCE" \
    --output "$ASSET_LOCK" > "$LOCK_ROOT/asset-lock-generation.json"
fi

readarray -t BOOTSTRAP_FIELDS < <(python3 - "$BOOTSTRAP" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(value["budget_config"])
print(value["verifier_lock"])
PY
)
BUDGET_LOCK=${BOOTSTRAP_FIELDS[0]}
VERIFIER_LOCK=${BOOTSTRAP_FIELDS[1]}
for path in "$ASSET_LOCK" "$TOKENIZER_LOCK" "$BUDGET_LOCK" "$VERIFIER_LOCK" \
  "$EXP/data/ce_subset_20x10_course/manifest.json"; do
  test -f "$path" || { echo "missing_lock_or_manifest=$path" >&2; exit 2; }
done

if ! test -f "$CE_CONFIG"; then
  echo "phase=bind_ce_config"
  python3 -m ce_arm.cli bind-config \
    --template "$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.template.json" \
    --output "$CE_CONFIG" \
    --asset-lock "$ASSET_LOCK" \
    --course-manifest "$EXP/data/ce_subset_20x10_course/manifest.json" \
    --shared-actor-config "$ACTOR" \
    --shared-budget-config "$BUDGET_LOCK" \
    --tokenizer-lock "$TOKENIZER_LOCK" \
    --trusted-verifier-lock "$VERIFIER_LOCK"
fi

echo "CE_CONFIG=$CE_CONFIG"
sha256sum "$CE_CONFIG" "$ASSET_LOCK" "$TOKENIZER_LOCK" "$ACTOR" \
  "$BUDGET_LOCK" "$VERIFIER_LOCK"

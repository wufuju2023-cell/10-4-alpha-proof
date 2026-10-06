# subset_20x10 formal wave commands

Remote experiment root:

```bash
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
export PYTHONPATH="$EXP/workstreams/ce_arm/src:$EXP/workstreams/online_v2_arm/src"
```

Bind the CE config once (the output path must not already exist):

```bash
ASSET_LOCK=/absolute/path/to/current/asset_lock.json
BUDGET_LOCK=/absolute/path/to/current/shared_budget.json
TOKENIZER_LOCK=/absolute/path/to/current/tokenizer_lock.json
VERIFIER_LOCK=/absolute/path/to/current/trusted_verifier_lock.json
for p in "$ASSET_LOCK" "$BUDGET_LOCK" "$TOKENIZER_LOCK" "$VERIFIER_LOCK"; do test -f "$p" || exit 2; done
python3 -m ce_arm.cli bind-config \
  --template "$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.template.json" \
  --output "$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json" \
  --asset-lock "$ASSET_LOCK" \
  --course-manifest "$EXP/data/ce_subset_20x10_course/manifest.json" \
  --shared-actor-config "$EXP/workstreams/ce_arm/config/shared_actor.real_7b_smoke.json" \
  --shared-budget-config "$BUDGET_LOCK" \
  --tokenizer-lock "$TOKENIZER_LOCK" \
  --trusted-verifier-lock "$VERIFIER_LOCK"
CE_CONFIG="$EXP/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json"
CE_CONFIG_SHA=$(sha256sum "$CE_CONFIG" | awk '{print $1}')
```

Wave 1 CE, after the cumulative 20-problem signed CE receipt bundle exists:

```bash
CE_RECEIPTS="$EXP/runs/formal/wave_001/ce/canonical-search-receipts.jsonl"
CE_RECEIPTS_SHA=$(sha256sum "$CE_RECEIPTS" | awk '{print $1}')
python3 -m ce_arm.formal \
  --config "$CE_CONFIG" --expected-config-sha256 "$CE_CONFIG_SHA" \
  --receipts "$CE_RECEIPTS" --expected-receipts-sha256 "$CE_RECEIPTS_SHA" \
  --run-dir "$EXP/runs/formal/wave_001/ce/update" \
  --learner-dir "$EXP/runs/formal/ce-learner" --wave-index 1
```

Wave 1 Online-v2 config requires exactly 20 repeated joined/signed pairs from
the same wave and one common behavior identity.  The orchestrator expands the
pair list into repeated CLI flags:

```bash
python3 "$EXP/workstreams/online_v2_arm/scripts/prepare_real_one_update_config.py" \
  --mode formal \
  --joined JOIN_01 --signed-receipt SIGNED_01 \
  ... \
  --joined JOIN_20 --signed-receipt SIGNED_20 \
  --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --adapter "$EXP/assets/initial_lora_r16_a32_seed20261004" \
  --value-head /mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt \
  --target-repo "$EXP/src/10-4-alpha-proof" \
  --behavior-adapter-name FORMAL_BEHAVIOR_ALIAS \
  --output "$EXP/runs/formal/wave_001/online-v2/config.json"
ONLINE_CONFIG="$EXP/runs/formal/wave_001/online-v2/config.json"
ONLINE_CONFIG_SHA=$(sha256sum "$ONLINE_CONFIG" | awk '{print $1}')
python3 "$EXP/workstreams/online_v2_arm/scripts/run_real_one_update.py" \
  --config "$ONLINE_CONFIG" --expected-config-sha256 "$ONLINE_CONFIG_SHA" \
  --output "$EXP/runs/formal/wave_001/online-v2/update"
```

Per wave the orchestrator must provide only:

1. `wave_index` (1..8);
2. CE cumulative signed receipt bundle path and SHA-256;
3. Online-v2's 20 `(joined, signed_receipt)` path pairs;
4. current arm checkpoint/adapter inputs (wave 1 uses the shared initial adapter;
   later waves use the preceding arm-specific checkpoint);
5. arm-specific output roots and, for CE waves 2..8, the preceding checkpoint path.

The missing outer control loop is responsible for generating those receipts
with each arm's current checkpoint.  Reusing wave-1 receipts or the shared
initial adapter in later waves is forbidden.

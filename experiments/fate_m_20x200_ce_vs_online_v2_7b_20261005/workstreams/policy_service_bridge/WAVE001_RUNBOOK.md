# Wave 001: 20-family one-step bridge

This is the shortest deployable 20-family gate from the current code. It uses
variant `v001` from every frozen family, captures each real Reap root state,
loads REAL-Prover 7B and the initial r16 adapter exactly once, then runs the 20
Lean sessions sequentially. All sessions share one actor config, behavior
identity and fixed PEFT alias. Actor unwarped log-prob rescoring is fixed to
micro-batch 1 to match Online-v2; the learner tolerance stays `0.0005`.

Remote launch:

```bash
cd /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
bash scripts/run_wave001_bridge.sh
```

The script prints the immutable run root. It first produces
`root-state-capture/root_state_pins.json`, then writes one join-compatible run
root per problem under `actor/<session_id>/`.

After the actor root is terminal `DONE`, use the wave adapter.  It calls the
existing canonical candidate collector and hardened join/strict replay/two-arm
converters for each of the 20 sessions.  It never writes below `ACTOR_ROOT` and
rechecks every producer/pin hash before publishing its own `DONE.json`:

After every instance restart, first create a fresh ephemeral verifier key and
the matching content-addressed public pins. This is safe to re-run on the same
live instance:

```bash
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
bash "$EXP/scripts/bootstrap_formal_verifier.sh"
BOOTSTRAP_JSON=$(find "$EXP/runs/formal" -maxdepth 2 -name bootstrap.json -type f \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
BUDGET_CONFIG=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget_config"])' "$BOOTSTRAP_JSON")
VERIFIER_LOCK=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verifier_lock"])' "$BOOTSTRAP_JSON")
PRIVATE_KEY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["private_key_path"])' "$BOOTSTRAP_JSON")
COURSE_PROJECT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["course_project"])' "$BOOTSTRAP_JSON")
RUNTIME_RECEIPT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_receipt"])' "$BOOTSTRAP_JSON")
```

The private key exists only at `/tmp/fate-verifier-ed25519.key` with mode
`0600`; it is never copied into persistent storage. The public lock, shared
budget and bootstrap receipt live under
`$EXP/runs/formal/verifier-bootstrap-<runtime>-<public-key>/`.

```bash
EXP=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
ACTOR_ROOT="$RUN_ROOT/actor"
JOIN_ROOT="$RUN_ROOT/ce-wave001-join"   # use a distinct root for each arm
LAKE="$EXP/runtime/toolchains/lean-4.28.0-linux/bin/lake"

export PYTHONUNBUFFERED=1
python3 "$EXP/workstreams/policy_service_bridge/scripts/run_wave_join_adapter.py" \
  --actor-root "$ACTOR_ROOT" \
  --plan "$EXP/workstreams/policy_service_bridge/config/wave001_20family_plan.frozen.json" \
  --wave-index 1 \
  --tokenizer-dir /mnt/workspace/models/REAL-Prover-fe76f68d \
  --budget-config "$BUDGET_CONFIG" \
  --runtime-receipt "$RUNTIME_RECEIPT" \
  --course-project "$COURSE_PROJECT" \
  --course-manifest "$COURSE_PROJECT/lake-manifest.json" \
  --lake "$LAKE" \
  --verifier-lock "$VERIFIER_LOCK" \
  --private-key "$PRIVATE_KEY" \
  --workspace-root /mnt/workspace \
  --problems "$EXP/data/problems.jsonl" \
  --output-root "$JOIN_ROOT"
```

`$JOIN_ROOT/training-inputs/ce-receipts.jsonl` is this wave's CE receipt source.
`$JOIN_ROOT/training-inputs/online-v2-receipt-pins.json` is the exact 20-entry
list for `pins.receipts` in Online-v2's frozen real-runner config.  Consume
neither file unless `$JOIN_ROOT/DONE.json` exists and has `state: DONE`.

Scientific scope: current canonical joining is one policy request / one state /
one linear proof only. This runner is therefore the required 20-problem
one-update gate, not the frozen protocol's full BestFirstSearch wave. Calling
it a formal full-search result would be incorrect until multi-request,
multi-state canonical envelopes and the pre-registered per-request seed map are
implemented.

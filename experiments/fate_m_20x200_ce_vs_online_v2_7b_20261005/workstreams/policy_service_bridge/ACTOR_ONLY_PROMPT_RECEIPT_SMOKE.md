# Actor-only official-prompt receipt smoke

This is the safe GPU gate to run while the Lean/Reap runtime is unavailable.
It performs no download, Lean invocation, training, optimizer step, or
backward pass. It replays one previously captured real Reap request, applies
the pinned upstream `PromptManage` at the policy-service boundary, runs the
formal initial r16 REAL-Prover actor, and commits one immutable
`fate.policy_request.v2` receipt.

The replay input is
`config/fate_m_003_v001.real_reap_request.v1.json` (SHA-256
`57eb286334dcfe45de6e89db6a42555a0bda23e366dff431fd6771105f04b0dc`).
It binds the source receipt file/payload hashes, normalized request hash,
incoming prompt hash, and parsed root-state hash. Any mismatch fails before
the model is loaded. The upstream prompt builder is also supplied with an
explicit expected file hash.

Run from the remote experiment root:

```bash
set -euo pipefail
E=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
RUN=/tmp/fate-m-actor-only-prompt-receipt-$(date +%Y%m%dT%H%M%S)
LOG="$E/runs/live_remote/actor_only_prompt_receipt_smoke.log"
cd "$E"
scripts/run_with_heartbeat.sh actor-only-prompt-receipt 900 "$LOG" -- \
  python workstreams/policy_service_bridge/scripts/actor_only_prompt_receipt_smoke.py \
    --model /mnt/workspace/models/REAL-Prover-fe76f68d \
    --adapter "$E/assets/initial_lora_r16_a32_seed20261004" \
    --problems "$E/data/problems.jsonl" \
    --prompt-builder "$E/src/REAL-Prover-upstream/Realprover/manager/manage/prompt_manage.py" \
    --prompt-builder-sha256 7e84f4892824106a07ed5f969fed555aa11a3411cd9596858d0d989c5dec2ed3 \
    --incoming-artifact "$E/workstreams/policy_service_bridge/config/fate_m_003_v001.real_reap_request.v1.json" \
    --incoming-artifact-sha256 57eb286334dcfe45de6e89db6a42555a0bda23e366dff431fd6771105f04b0dc \
    --output-dir "$RUN"
printf 'ACTOR_ONLY_RUN=%s\n' "$RUN"
python - "$RUN" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
report = json.loads((root / "report.json").read_text(encoding="utf-8"))
assert report["result"] == "PASS"
assert all(report["verification"]["conditions"].values())
assert report["verification"]["candidate_count"] == 64
print(json.dumps({"run": str(root), "verification": report["verification"]}, sort_keys=True))
PY
```

Expected output is a new `/tmp` run directory containing `report.json`,
`DONE.json`, `STATE.json`, `STATE.final.json`, and exactly one receipt below
`actor_receipts/fate_m_003_v001/policy_requests/`. The receipt must show
`prompt_binding.transform_applied=true`, and the report requires exact equality
among PromptManage output, the generator's actual prompt/hash/token IDs,
direct tokenizer output, and the committed receipt.

Prior full-shape formal captures used about 38.9 GB allocated / 57.4 GB
reserved GPU memory, around 10-12 seconds for generation plus rescore, and
well under 1 MB of persistent evidence. The base-model load makes the overall
wall time roughly one to two minutes. This smoke writes its evidence under
`/tmp`; only the concise heartbeat log touches persistent storage.

Passing this gate proves the current transform -> actor -> immutable receipt
boundary for a replayed, hash-pinned real Reap request. It does not prove that
a newly launched Reap/Lean process emits the same request, and therefore does
not replace the later live one-task Reap/Lean parity smoke.

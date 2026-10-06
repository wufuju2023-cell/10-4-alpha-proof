#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
E=/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005
OLD="$E/runs/formal-20x10"
R="$E/runs/formal-20x10-corrected"
H="$R/heldout-eval"
JOIN=/tmp/fate-m-formal-w001-join-v2/training-inputs/manifest.json
mkdir -p "$R/control"
trap 'rc=$?; printf "{\"state\":\"FAILED\",\"exit_code\":%s}\n" "$rc" > "$R/control/corrected_chain_FAILED.json"; exit "$rc"' ERR

# Preserve the old CE evaluation and its absolute-path receipts untouched.
# Both stopped legacy controllers must remain stopped: neither is authoritative.
while [[ ! -f "$OLD/heldout-eval/ce_final/DONE.json" && ! -f "$R/control/diagnostic_ce_released.json" ]]; do
  test ! -f "$OLD/heldout-eval/ce_final/FAILED.json"
  test ! -f "$OLD/heldout-eval/ce_final/INCOMPLETE.json"
  echo "{\"event\":\"corrected_chain_heartbeat\",\"stage\":\"waiting_for_diagnostic_ce_eval\",\"unix_time\":$(date +%s)}"
  sleep 20
done

python3 - "$E" "$R" "$JOIN" <<'PY'
import hashlib,json,pathlib,sys,time,shutil
e,r,j=map(pathlib.Path,sys.argv[1:])
old=e/'runs/formal-20x10'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
pins={
 'ce_config':(e/'workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json','e4191e169cc7a7e705b7daace34e337d844b636bffc0bd0b777974953867586f'),
 'ce_trainer':(e/'workstreams/ce_arm/src/ce_arm/train.py','6065b961b613813290c80386862d7d81f5589ce3b45323d19bfebbd7756ff2e3'),
 'online_config':(old/'wave_001/online-v2/config.json','f37680c51d0b37ac9fa8c1f906eedf3b1cfa2eff4ecfb8bd387d464898479729'),
 'join':(j,'193da051e116bce7b73433bf687d75e3854c82bbbb88818dc175024380c75241'),
}
for name,(p,h) in pins.items(): assert sha(p)==h,(name,sha(p))
g=json.loads((old/'control/terminal_q_loader_validation.json').read_text())
assert g['state']=='PASS' and g['samples']==233 and g['positive_advantages']==49 and g['negative_advantages']==184
assert g['repaired_wave_sha256']=='ab2cccc55ada03ea1e7298935b5760a9b4bd84b00a256607bd367689c31dcb36'
assert shutil.disk_usage(e).free > 3*1024**3, 'insufficient checkpoint reserve'
for src,dst in [(pins['online_config'][0],r/'wave_001/online-v2/config.json'),(old/'heldout-eval/root_state_pins.json',r/'heldout-eval/root_state_pins.json')]:
 dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.exists(): assert dst.read_bytes()==src.read_bytes()
 else: dst.write_bytes(src.read_bytes())
out={'state':'FROZEN','unix_time':time.time(),'pins':{k:{'path':str(p),'sha256':h} for k,(p,h) in pins.items()},'initial_report_sha256':sha(old/'heldout-eval/initial/report.json'),'old_ce_role':'one-sample diagnostic only; excluded from corrected comparison','old_online_role':'zero-advantage diagnostic archived','scope':'20 training tasks, one shared wave; original minimum40/two-wave protocol INCOMPLETE','fresh_ce_replay_and_learner':True,'source_receipts_modified':False}
p=r/'control/corrected_execution_pins.json'
if not p.exists(): p.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
print(json.dumps({'event':'corrected_inputs_frozen','run_root':str(r)}),flush=True)
PY

mkdir -p "$R/wave_001"
python3 "$E/workstreams/orchestration/scripts/run_paired_train_wave.py" \
  --wave-index 1 --ce-join-manifest "$JOIN" --online-join-manifest "$JOIN" \
  --ce-config "$E/workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json" \
  --run-root "$R" --model /mnt/workspace/models/REAL-Prover-fe76f68d \
  --target-repo "$E/src/10-4-alpha-proof" --arm-order ce-first \
  2>&1 | tee -a "$R/wave_001/paired-train.log"

python3 - "$R" <<'PY'
import json,pathlib,sys
r=pathlib.Path(sys.argv[1]); c=json.loads((r/'wave_001/ce/update/FORMAL_DONE.wave_001.json').read_text())
u=json.loads((pathlib.Path(c['checkpoint'])/'receipt.json').read_text())
assert c['global_step']==1 and u['samples']==20 and len(set(u['selected_transition_ids']))==20
assert u['learner_action_tokens']==137,u
o=json.loads((r/'wave_001/online-v2/update/DONE.json').read_text()); v=o['update_receipt']
assert v['accepted'] and v['optimizer_steps_this_update']>0
assert v['wave_digest']=='ab2cccc55ada03ea1e7298935b5760a9b4bd84b00a256607bd367689c31dcb36'
assert o['pre_policy_sha256']!=o['post_policy_sha256']
out={'state':'PASS','ce_receipt':u,'online_update_receipt':v}
(r/'control/effective_training_gate.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
print(json.dumps({'event':'corrected_training_asserted',**out}),flush=True)
PY

export FATE_HELDOUT_ROOT="$H"
bash "$E/scripts/launch_heldout_eval.sh" ce_final "$R/wave_001/ce"
bash "$E/scripts/launch_heldout_eval.sh" online_final "$R/wave_001/online-v2"
python3 "$E/scripts/summarize_heldout_comparison.py" \
  --initial "$OLD/heldout-eval/initial" --ce-final "$H/ce_final" \
  --online-final "$H/online_final" --output-dir "$H/comparison"
test -s "$H/comparison/DONE.json"
printf '{"state":"DONE","scope":"20 training tasks, one wave; original protocol INCOMPLETE"}\n' > "$R/control/corrected_chain_DONE.json"

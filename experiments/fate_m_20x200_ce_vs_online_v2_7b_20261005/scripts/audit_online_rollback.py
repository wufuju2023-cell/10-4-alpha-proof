#!/usr/bin/env python3
"""Prove the rejected update restored policy/value/optimizer before reusing baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tensor_hash(state):
    h = hashlib.sha256()
    for k, t in sorted(state.items()):
        t = t.detach().cpu().contiguous()
        h.update(json.dumps([k, str(t.dtype), list(t.shape)], separators=(',', ':')).encode())
        h.update(t.view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def equal(a, b):
    if torch.is_tensor(a):
        return torch.is_tensor(b) and a.dtype == b.dtype and torch.equal(a.cpu(), b.cpu())
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, (tuple, list)):
        return type(a) == type(b) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if type(a).__module__.startswith('numpy'):
        import numpy as np
        return np.array_equal(a, b)
    return a == b


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment-root', type=Path, required=True)
    args = parser.parse_args()
    e = args.experiment_root.resolve()
    sys.path.insert(0, str(e/'workstreams/online_v2_arm/src'))
    from alphaproof_online_v2_arm.real_runner import _load_value_head
    r = e/'runs/formal-20x10-corrected'
    u = r/'wave_001/online-v2/update'
    receipt_path = u/'ROLLED_BACK.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['state'] == 'ROLLED_BACK'
    assert receipt['update_receipt']['accepted'] is False
    assert receipt['update_receipt']['reason'] == 'post_step_behavior_kl_limit'
    assert receipt['update_receipt']['optimizer_steps_total'] == 0
    assert receipt['pre_policy_sha256'] == receipt['post_policy_sha256']
    checkpoint = Path(receipt['checkpoint']['path'])
    manifest = json.loads((checkpoint/'manifest.json').read_text())
    assert sha(checkpoint/'manifest.json') == receipt['checkpoint']['manifest_sha256']
    for f in manifest['files']:
        p = (checkpoint/f['path']).resolve()
        assert p.is_relative_to(checkpoint.resolve()) and sha(p) == f['sha256']
        assert p.stat().st_size == f['size']
    initial = e/'assets/initial_lora_r16_a32_seed20261004'
    deployed = checkpoint/'adapter/online_v2_policy'
    assert sha(initial/'adapter_model.safetensors') == sha(deployed/'adapter_model.safetensors')
    c1 = json.loads((initial/'adapter_config.json').read_text())
    c2 = json.loads((deployed/'adapter_config.json').read_text())
    differences = {k: [c1.get(k), c2.get(k)] for k in c1.keys() | c2.keys() if c1.get(k) != c2.get(k)}
    assert set(differences) <= {'target_modules'}, differences
    if differences:
        assert set(c1['target_modules']) == set(c2['target_modules'])
    ledger_path = u/'wave_ledger.json'
    ledger = json.loads(ledger_path.read_text())['waves']['wave-0001']
    assert ledger['status'] == 'ABORTED' and ledger['abort_reason'] == receipt['update_receipt']['reason']
    before_path = Path(ledger['before_checkpoint']['path'])
    assert sha(before_path) == ledger['before_checkpoint']['sha256']
    before = torch.load(before_path, map_location='cpu', weights_only=False)
    training_path = checkpoint/'training_state.pt'
    assert sha(training_path) == receipt['checkpoint']['resume_training_state']['sha256']
    after = torch.load(training_path, map_location='cpu', weights_only=False)
    assert before.optimizer_steps == after['optimizer_steps'] == 0
    assert equal(before.optimizer, after['optimizer']) and after['optimizer']['state'] == {}
    for key in ('scheduler', 'python_rng', 'torch_rng', 'cuda_rng', 'numpy_rng', 'behavior_kl_beta'):
        assert equal(getattr(before, key), after[key]), key
    value_path = checkpoint/'value_head.pt'
    deployed_value = torch.load(value_path, map_location='cpu', weights_only=True)
    initial_value_path = Path('/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt')
    base_config = json.loads(Path('/mnt/workspace/models/REAL-Prover-fe76f68d/config.json').read_text())
    head = _load_value_head(e/'src/10-4-alpha-proof', initial_value_path, int(base_config['hidden_size']), torch.device('cpu'))
    assert equal(head.state_dict(), deployed_value)
    assert all(equal(deployed_value[k], v) for k, v in before.value_parameters.items())
    initial_report = e/'runs/formal-20x10/heldout-eval/initial/report.json'
    initial_eval_config = json.loads(initial_report.with_name('config.json').read_text())
    assert initial_eval_config['adapter_model_sha256'] == sha(initial/'adapter_model.safetensors')
    assert initial_eval_config['adapter_config_sha256'] == sha(initial/'adapter_config.json')
    model_lock_path = Path('/mnt/workspace/models/REAL-Prover-fe76f68d/reap-model-lock.json')
    model_lock = json.loads(model_lock_path.read_text())
    assert model_lock['revision'] == initial_eval_config['base_model_revision']
    assert model_lock['verified_against']['canonical_manifest_sha256'] == initial_eval_config['base_model_manifest_sha256']
    out = {
        'state': 'PASS', 'online_update_accepted': False,
        'reason': receipt['update_receipt']['reason'],
        'receipt_path': str(receipt_path), 'receipt_sha256': sha(receipt_path),
        'pre_policy_sha256': receipt['pre_policy_sha256'], 'post_policy_sha256': receipt['post_policy_sha256'],
        'initial_adapter_model_sha256': sha(initial/'adapter_model.safetensors'),
        'deployed_adapter_model_sha256': sha(deployed/'adapter_model.safetensors'),
        'initial_adapter_config_sha256': sha(initial/'adapter_config.json'),
        'deployed_adapter_config_sha256': sha(deployed/'adapter_config.json'),
        'adapter_config_differences': differences,
        'configuration_semantically_identical': True,
        'value_head_initial_file_sha256': sha(initial_value_path), 'value_head_deployed_file_sha256': sha(value_path),
        'value_head_tensor_sha256': tensor_hash(deployed_value),
        'value_head_tensors_exactly_restored': True, 'optimizer_and_rng_exactly_restored': True,
        'optimizer_steps_committed': 0, 'ledger_status': ledger['status'],
        'ledger_sha256': sha(ledger_path), 'before_checkpoint_sha256': sha(before_path),
        'checkpoint_manifest_sha256': sha(checkpoint/'manifest.json'),
        'initial_report_path': str(initial_report), 'initial_report_sha256': sha(initial_report),
        'base_model_lock_sha256': sha(model_lock_path),
        'baseline_reuse_allowed': True,
        'reuse_kind': 'exact policy weights and semantically identical PEFT config; target_modules ordering only',
        'independent_online_heldout_evaluation': False,
    }
    p = r/'control/online_rollback_identity_audit.json'
    assert not p.exists(), 'audit is immutable'
    p.write_text(json.dumps(out, sort_keys=True, indent=2)+'\n')
    print(json.dumps(out, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()

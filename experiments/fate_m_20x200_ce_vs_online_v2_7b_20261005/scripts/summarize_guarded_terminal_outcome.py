#!/usr/bin/env python3
"""Report CE and a rejected Online update without inventing an Online evaluation."""
import argparse
import copy
import json
from pathlib import Path

from summarize_heldout_comparison import load_report, summarize, sha256_file, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment-root', type=Path, required=True)
    args = parser.parse_args()
    e = args.experiment_root.resolve()
    r = e/'runs/formal-20x10-corrected'
    audit_path = r/'control/online_rollback_identity_audit.json'
    audit = json.loads(audit_path.read_text())
    assert audit['state'] == 'PASS' and audit['baseline_reuse_allowed'] is True
    assert audit['online_update_accepted'] is False
    assert audit['value_head_tensors_exactly_restored'] and audit['optimizer_and_rng_exactly_restored']
    assert audit['initial_adapter_model_sha256'] == audit['deployed_adapter_model_sha256']
    assert audit['pre_policy_sha256'] == audit['post_policy_sha256']
    assert sha256_file(Path(audit['receipt_path'])) == audit['receipt_sha256']
    initial_path = e/'runs/formal-20x10/heldout-eval/initial'
    ce_path = r/'heldout-eval/ce_final'
    assert sha256_file(initial_path/'report.json') == audit['initial_report_sha256']
    initial = load_report(initial_path, 'initial')
    ce = load_report(ce_path, 'ce_final')
    reused = copy.deepcopy(initial)
    reused['checkpoint_label'] = 'online_final'
    reused['wall_seconds'] = 0.0
    out = summarize({'initial': initial, 'ce_final': ce, 'online_final': reused})
    rollback = json.loads(Path(audit['receipt_path']).read_text())
    ce_done_path = r/'wave_001/ce/update/FORMAL_DONE.wave_001.json'
    ce_done = json.loads(ce_done_path.read_text())
    ce_receipt = json.loads((Path(ce_done['checkpoint'])/'receipt.json').read_text())
    assert ce_receipt['samples'] == 20 and ce_receipt['learner_action_tokens'] == 137
    out['schema_version'] = 'fate.guarded_terminal_outcome.v1'
    out['conclusion'] = {
        'descriptive_winner': 'not_applicable_online_update_rejected',
        'claim_limit': 'Online update was rejected by the frozen KL guard and exactly rolled back. '
                       'Its deployed-policy result reuses initial evidence by verified identity, '
                       'not an independent Online heldout run. CE versus baseline is descriptive '
                       'single-seed within-family transfer; no accepted CE-versus-Online learning comparison, '
                       'significance, robustness, or algorithm-superiority claim is supported.',
    }
    out['execution_scope'] = {
        'training_tasks': 20, 'common_waves': 1,
        'original_adaptive_protocol': 'INCOMPLETE: minimum40 training tasks/two waves not met',
        'both_accepted_updates_gate': False,
    }
    out['training_updates'] = {
        'ce': {'accepted': True, 'terminal_sha256': sha256_file(ce_done_path), 'receipt': ce_receipt},
        'online_v2': {'accepted': False, 'state': rollback['state'],
                      'terminal_sha256': audit['receipt_sha256'], 'receipt': rollback['update_receipt']},
    }
    out['online_result_provenance'] = {
        'kind': 'identity_reuse_of_initial', 'independent_heldout_run': False,
        'incremental_heldout_requests': 0, 'incremental_heldout_gpu_seconds': 0,
        'initial_report_path': str(initial_path/'report.json'),
        'initial_report_sha256': audit['initial_report_sha256'],
        'identity_audit_path': str(audit_path), 'identity_audit_sha256': sha256_file(audit_path),
        'adapter_weights_byte_identical': True,
        'peft_config_semantically_identical': True,
        'config_bytes_identical': audit['initial_adapter_config_sha256'] == audit['deployed_adapter_config_sha256'],
        'config_difference': 'target_modules ordering only',
        'initial_evaluation_wall_seconds': initial['wall_seconds'],
    }
    output = e/'derived/results/corrected_terminal_comparison'
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output/'comparison.json', out)
    rows = out['checkpoints']
    lines = ['# Guarded single-wave terminal outcome', '',
             '| policy | solved /40 | pass@1 | pass@2 | pass@4 | evidence |',
             '|---|---:|---:|---:|---:|---|']
    for label, title, evidence in [
        ('initial', 'initial', 'independent40-task evaluation'),
        ('ce_final', 'corrected CE', 'independent40-task evaluation'),
        ('online_final', 'Online rollback deployment', 'initial evidence reused by exact policy identity'),
    ]:
        m = rows[label]
        lines.append(f"| {title} | {m['solved_count']}/40 | {m['pass_at_1']:.3f} | {m['pass_at_2']:.3f} | {m['pass_at_4']:.3f} | {evidence} |")
    lines += ['', out['conclusion']['claim_limit'], '',
              'Original protocol and both-accepted-update scope: **INCOMPLETE**.', '']
    (output/'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    terminal = {'state': 'DONE', 'terminal_outcome_report': str(output/'comparison.json'),
                'comparison_sha256': sha256_file(output/'comparison.json'),
                'original_protocol_complete': False, 'both_updates_accepted': False,
                'online_result_source': 'identity reuse of initial; no independent rerun'}
    atomic_json(r/'control/terminal_outcome_DONE.json', terminal)
    print(json.dumps(terminal, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()

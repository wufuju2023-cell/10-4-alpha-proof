#!/usr/bin/env python3
"""Bundle immutable small run evidence; inventory retained checkpoints separately."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-running', action='store_true')
    args = parser.parse_args()
    e = args.experiment_root.resolve()
    r = e / 'runs/formal-20x10-corrected'
    if not args.allow_running:
        assert ((r / 'control/corrected_chain_DONE.json').is_file()
                or (r / 'control/terminal_outcome_DONE.json').is_file()), 'comparison not terminal'
    assert not args.output.exists(), 'never overwrite an evidence archive'
    roots = [r, e / 'runs/formal-20x10/heldout-eval/initial',
             e / 'archive/formal-wave001-ce-one-sample-20261006',
             e / 'derived/results/corrected_terminal_comparison']
    source_paths = [
        'config/single_wave_execution_scope.json',
        'scripts/run_corrected_single_wave_comparison.sh',
        'scripts/evaluate_heldout_checkpoint.py',
        'scripts/summarize_heldout_comparison.py',
        'scripts/launch_heldout_eval.sh',
        'scripts/collect_corrected_evidence.py',
        'scripts/run_guarded_terminal_report.sh',
        'scripts/audit_online_rollback.py',
        'scripts/summarize_guarded_terminal_outcome.py',
        'workstreams/ce_arm/config/ce_arm.subset_20x10.formal.json',
        'workstreams/ce_arm/src/ce_arm/train.py',
        'workstreams/online_v2_arm/src/alphaproof_online_v2_arm/builder.py',
        'workstreams/online_v2_arm/src/alphaproof_online_v2_arm/learner.py',
        'workstreams/orchestration/scripts/run_paired_train_wave.py',
    ]
    files = {e / p for p in source_paths}
    checkpoint_files = []
    for root in roots:
        assert root.resolve().is_relative_to(e)
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            if p.suffix in {'.json', '.jsonl', '.lean', '.log', '.md', '.txt'} and p.stat().st_size <= 8 * 1024 * 1024:
                files.add(p)
            elif p.suffix in {'.pt', '.safetensors', '.bin'}:
                checkpoint_files.append({'path': str(p), 'bytes': p.stat().st_size,
                                         'sha256': sha256(p), 'retained_remote': True})
    index = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'x', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):
            assert p.is_file(), p
            assert p.resolve().is_relative_to(e), p
            raw = p.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            name = 'remote/' + p.as_posix().lstrip('/')
            z.writestr(name, raw)
            index[str(p)] = {'member': name, 'bytes': len(raw), 'sha256': digest}
        z.writestr('INDEX.json', json.dumps({
            'schema': 'fate.corrected_evidence_bundle.v1',
            'scope': '20 training tasks, one wave; original protocol INCOMPLETE',
            'snapshot_of_running_job': args.allow_running,
            'files': index, 'checkpoint_files_retained_remote': checkpoint_files,
        }, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'state': 'BUNDLED', 'path': str(args.output),
                      'sha256': sha256(args.output), 'bytes': args.output.stat().st_size,
                      'files': len(index), 'checkpoint_files': len(checkpoint_files)}, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()

# Experiment orchestration workstream

This directory contains the reviewed fail-closed supervisor and the thin paired
production adapter for the FATE-M CE versus Online-v2 experiment.

## Paired CE / Online-v2 launch

`src/paired_experiment.py` freezes one shared seed, course manifest, initial
adapter manifest, live signed canonical receipt (or a formal receipt-set
manifest), and actor config into both arms. It
requires exactly one CE and one Online-v2 stage for every declared mode/wave.
The single-GPU production policy is deliberately sequential: this prevents two
7B model loads from competing for the same accelerator while still dispatching
the second arm immediately after the first.

`src/paired_stage_worker.py` adapts each arm's native command to the supervisor
receipt contract. It commits a small unit-0 control checkpoint before invoking
the real arm, preserves that exact checkpoint after failure, passes the arm's
native resume argument on retry, and commits unit 1 plus a hash-bound result
only after the native terminal receipt and checkpoint exist. Model checkpoints
are not copied into the control directory.

The production template is
`config/paired_protocol.production.template.json`. Its `<...>` fields are hard
stops, not defaults. Bind them only from the current live Lean/Reap join and the
two finalized one-update configs; do not reuse the older actor receipt.

Shortest remote sequence after those inputs exist:

```bash
cd /mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005

# Replace every <...> token in a copied protocol, then freeze it fail-closed.
python3 workstreams/orchestration/src/paired_experiment.py freeze \
  --protocol workstreams/orchestration/config/paired_protocol.smoke.json \
  --output workstreams/orchestration/config/orchestrator.paired.smoke.json

# The browser controller must transcribe a genuinely fresh top-right UI value.
python3 workstreams/orchestration/src/experiment_orchestrator.py storage-sample \
  --state-dir runs/control --used-gib 88.6 --capacity-gib 100 \
  --source modelscope_ui_top_right

# One CE update followed immediately by one Online-v2 update from identical inputs.
python3 workstreams/orchestration/src/paired_experiment.py run-pair \
  --protocol workstreams/orchestration/config/paired_protocol.smoke.json \
  --config workstreams/orchestration/config/orchestrator.paired.smoke.json \
  --mode smoke --wave-start 1 --wave-end 1
```

After an interruption, rerun only the last command with `--resume`. A completed
first arm is revalidated and skipped; a failed arm resumes from its native
checkpoint. To scale, append paired `formal` stages for waves 1..175 to the
same protocol and select a bounded range with `--mode formal --wave-start N
--wave-end M`.

Current launch-blocking inputs (the orchestration code itself is no longer one):

1. the new current-actor live Lean/Reap signed receipt and both derived
   arm inputs for wave 1;
2. a frozen CE config and a frozen Online-v2 real-one-update config, including
   their exact file SHA-256 values;
3. the real CE replay bundle path produced from that same signed receipt set;
4. a continuously refreshed ModelScope top-right storage observation while a
   stage runs.

Focused evidence: `python -m unittest discover -s tests -v` currently discovers
23 tests; on Windows 21 pass and two POSIX lifecycle tests skip. The paired
subset includes a full synthetic supervisor → worker → native arm → checkpoint
and success-receipt run, plus fail-then-resume without a same-unit fork.

## Guarantees

- ModelScope's top-right persistent-storage value is the sole quota authority;
  there is no `df` fallback. UI observations expire after at most 60 seconds
  (the example uses 45 seconds).
- The 100 GiB capacity and 90/95 GiB thresholds are frozen. Every stage declares
  `max_additional_gib`. Admission requires `used + reservation < 95`; expensive
  stages are also refused anywhere in the 90 GiB warning region.
- A storage observation is checked every second and whenever a checkpoint
  receipt changes. The browser controller must keep refreshing the observation
  file during the run.
- Linux first holds a `PDEATHSIG` stage wrapper behind a launch gate, starts an
  independent pipe watchdog, and only then releases the real command. Abrupt
  supervisor death closes the pipe and the watchdog kills the complete PGID,
  including already-forked grandchildren. Windows uses `CREATE_SUSPENDED`,
  assigns the process to a kill-on-close Job Object, then resumes its sole
  primary thread; the child is never runnable before Job assignment.
- `SIGINT`, `SIGTERM`, and `SIGHUP` trigger bounded tree shutdown and a `FAILED`
  receipt. A lease records boot ID, supervisor PID identity, child PID and PGID.
  A dead lease is only recovered automatically after proving both owner and
  process group are gone.
- A preallocated control-space reserve is released before final receipts. State
  replacement fsyncs both file and directory. Receipt failures produce a
  separate emergency receipt (normally on `/tmp`) and cannot strand the lock.
- `DONE` requires more than exit code zero: every declared immutable input and
  selected inherited environment value is fingerprinted, the child success
  receipt must echo those fingerprints, and every required output is rehashed.
  On later resume, the entire recalculated evidence (including success-receipt
  SHA and all output SHA/size/path tuples) must equal the evidence sealed in the
  original orchestrator state; replacing artifact and worker receipt together
  is rejected.

## Storage observation

Record the current browser UI reading immediately before launch and refresh it
at least every 20–30 seconds while a stage is active:

```bash
python3 src/experiment_orchestrator.py storage-sample \
  --state-dir runs/control \
  --used-gib 88.81 --capacity-gib 100 \
  --source modelscope_ui_top_right
```

The observer is external by design: writing the same old number repeatedly is
not a refresh. It must transcribe a newly read UI value.

## Resume and checkpoint contract

`--resume` has two explicit meanings:

1. For a valid existing `DONE`, rehash inputs and outputs, validate the success
   receipt, then skip.
2. For an interrupted expensive stage, validate the child checkpoint plus its
   atomic receipt, append the configured `resume_args`, and continue after
   `last_committed_unit`.

Expensive stages cannot use restart-only resume. Their checkpoint receipt is:

```json
{
  "schema_version": 1,
  "status": "COMMITTED",
  "stage": "ce_seed0",
  "input_fingerprint": "<EXPERIMENT_INPUT_FINGERPRINT>",
  "stage_fingerprint": "<EXPERIMENT_STAGE_FINGERPRINT>",
  "last_committed_unit": 17,
  "checkpoint_sha256": "<sha256 of checkpoint_path>",
  "event_log_committed_bytes": 4567,
  "event_log_prefix_sha256": "<sha256 of exactly those bytes>"
}
```

The worker must commit one unit transactionally: obtain a fresh UI storage
sample/check before starting a large checkpoint, write the checkpoint to a
temporary name and fsync/rename it, then atomically write this receipt last.
`event_log_path` must be exactly the committed size when the receipt is checked;
extra bytes from a crash between event append and checkpoint commit fail closed.
The supervisor seals a monotonic checkpoint ledger after every attempt. A lower
unit is rollback; the same unit with different checkpoint, receipt, or event
evidence is a fork; both are rejected. The supervisor exposes the two
fingerprints and stage ID in environment variables.

Storage reservation is a total stage-growth bound, not a per-poll charge. At
admission the supervisor seals `projected_peak = start_used + max_additional`.
Later UI observations must remain below that peak and the absolute 95 GiB stop,
but do not add the reservation a second time.

If normal state finalization fails, no `DONE` attempt is appended. A separate
emergency receipt is marked authoritative, records `FAILED`, is surfaced by
`status`, and blocks resume until it is manually investigated. Control reserves
are per stage, so concurrent stages cannot unlink one another's reserve.

Successful workers atomically write `success_receipt` last:

```json
{
  "schema_version": 1,
  "status": "DONE",
  "stage": "ce_seed0",
  "input_fingerprint": "<EXPERIMENT_INPUT_FINGERPRINT>",
  "stage_fingerprint": "<EXPERIMENT_STAGE_FINGERPRINT>",
  "outputs": {
    "final_checkpoint": {"sha256": "<sha256>", "size_bytes": 123}
  }
}
```

Use `--restart` only for an intentional from-scratch rerun after the worker's
own output policy has made that safe. Without `--resume` or `--restart`, an
existing interrupted/failed state is refused.

## Commands

```bash
python3 src/experiment_orchestrator.py validate --config config/orchestrator.example.json
python3 src/experiment_orchestrator.py run --config CONFIG --stage STAGE
python3 src/experiment_orchestrator.py run --config CONFIG --stage STAGE --resume
python3 src/experiment_orchestrator.py status --config CONFIG
python3 src/experiment_orchestrator.py recover-lock --config CONFIG --stage STAGE
python3 -m unittest discover -s tests -v
```

`recover-lock` refuses a live owner or surviving owned POSIX process group. It
archives dead lock evidence rather than deleting it.

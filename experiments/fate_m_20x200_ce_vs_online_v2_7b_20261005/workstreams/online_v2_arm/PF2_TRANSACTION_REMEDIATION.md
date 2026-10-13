# PF-2 ledger/checkpoint transaction remediation

Status: implemented; reviewer reproductions pass locally.

## Transaction protocol

- The learner holds a path-scoped in-process `RLock` and OS advisory file lock
  for the complete update, including forward/backward, optimizer step and commit.
  State-changing store methods fail closed when invoked outside that lock.
- `begin()` writes a hashed `before.pt`, then records `PREPARED` with a random
  transaction ID, policy version and validated-wave digest.
- `commit()` writes a hashed `committed.pt` and hashed JSON update receipt, then
  rereads the ledger and performs a compare-and-swap on transaction ID, policy
  version and digest before switching the entry to `COMMITTED`.
- Startup/update reconciliation restores a valid `before.pt` for an interrupted
  PREPARED transaction and marks it ABORTED. ABORTED is also restored from its
  before-image idempotently, covering a crash between abort recording and the
  in-memory rollback. A COMMITTED transaction is usable
  only when both checkpoint and receipt hashes verify, and restores the post-update
  state before sealing the wave.
- Both checkpoints contain trainable policy/value parameters, optimizer,
  scheduler, Python/NumPy/CPU/CUDA RNG, optimizer-step counter and adaptive KL beta.

Artifacts live beside the ledger under `<ledger>.artifacts/`; ledger and receipt
JSON writes use write/fsync/atomic replace. Torch checkpoint writes use the same
temporary-file/fsync/replace discipline, including the Windows-compatible `rb+`
handle required by `FlushFileBuffers`.

## Reviewer reproductions now covered

- `test_commit_then_raise_recovers_committed_model_instead_of_rolling_back`:
  injects an exception immediately after durable commit and proves the learner and
  a restarted learner both retain the committed model, rather than permanently
  consuming the wave while rolling the model back.
- `test_two_learner_instances_competing_for_one_wave_only_commit_once`:
  starts two independent learner/ledger instances on the same wave; exactly one
  receives an accepted receipt and the loser observes the committed state.
- `test_prepared_crash_is_restored_and_wave_can_be_retried`:
  leaves PREPARED deliberately, starts with dirty policy state, proves startup
  restores the before checkpoint, and then successfully retries the wave.

Full local result: `50 passed`. Synthetic CPU smoke: `DONE`.

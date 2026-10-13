# PF-3 identity-chain remediation

Status: implemented and reviewer reproductions pass.

## Changes

- `SearchStateReceipt` now hashes and validates behavior adapter version/SHA-256
  and base model version/SHA-256. `policy_version` must equal the behavior
  version that actually generated the rollout.
- `RolloutSample` and `ValidatedRolloutWave.reference_identity` retain the same
  identity. Both the builder and pre-learner validation reject mixed identities
  anywhere in a wave.
- `OnlineV2Learner` calls
  `adapter_references.validate_wave_identity(**samples.reference_identity)` at
  the learner boundary, before forward/backward or optimizer mutation.
- `PeftSingleBackboneBackend` now requires `base_state_getter`; the backend
  hashes the returned live frozen tensors itself. It cannot accept a cached
  `ArtifactIdentity` receipt as the alleged live base check.

## Reviewer reproductions

- mixed base receipt SHA inside one wave is rejected by the builder;
- a spy validator proves the learner entry invokes wave identity validation;
- changing one frozen base tensor causes live base identity validation to fail
  before any optimizer step;
- constructing a PEFT backend without a live base-state getter is rejected.

Focused verification command:

```powershell
pytest -q tests/test_receipt_builder.py tests/test_adapter_reference.py tests/test_adapter_learner_integration.py::test_learner_entry_calls_wave_identity_validation tests/test_adapter_learner_integration.py::test_mutating_frozen_base_fails_before_learning
```

Focused result: `23 passed`.

Final full-suite result after the concurrent ledger work landed: `50 passed`.
PF-3 did not modify the ledger transaction implementation.

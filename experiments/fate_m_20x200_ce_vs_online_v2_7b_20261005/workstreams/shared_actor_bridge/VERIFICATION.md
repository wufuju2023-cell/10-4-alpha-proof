# Verification report

Date: 2026-10-05

## Current code-level status

The shared actor → per-raw Online-v2 receipt semantics pass targeted CPU test
suites. Every distinct full raw completion becomes one signed PPO row; exact
duplicate sequences retain draw multiplicity. Each row has the full completion
mask, warped `q_old`, audit-only `p_old`, candidate-local EOS convention, and a
signed mapping to its actual Lean execution result. Strict proof paths still
refer to signed executions and cannot count sibling raw rows as extra proof
steps.

These tests do not demonstrate a real 7B point-of-use sampling receipt or a
Reap/Lean signed end-to-end run. Formal training remains gated on those checks.
Historical frozen-shape model-capture evidence is in
`../../runs/preflight/real-actor-capture/REPORT.md`; it is performance and
determinism evidence, not validation of the new signed per-raw receipt path.

## Targeted test results (2026-10-05)

Commands were run from the workspace root:

```powershell
python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/online_v2_arm/tests experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/shared_actor_bridge/tests
# 86 passed

python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/ce_arm/tests
# 41 passed

python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests
# 9 passed

$env:PYTHONPATH='experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/lean_integration/src'; python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/lean_integration/tests
# 29 passed
```

Targeted total: **86 + 41 + 9 + 29 = 165 passed**. No source files under
`policy_service_bridge` were modified.

## Broad collection limitation

A broad `pytest` over `workstreams/` is not currently a valid aggregate gate:
collection fails before tests run, with 9 collection errors. The checked-in
`integration_bundle`/payload tests import package names such as `alphaproof`,
`update_offline`, `update_online_v2`, and `fate_reap.policy_receipts` that are
not installed or exposed on that broad invocation's import path; the tree also
contains colliding `test_bridge.py` basenames. Per the task scope, the bundle
was not refreshed. The four targeted suites above are the validated counts;
the broad collection failure is an infrastructure/import-configuration
limitation, not a test failure in those suites.

## Remaining gates

- Produce a real 7B point-of-use receipt with per-token `q_old` recomputed
  under the actual temperature=1.5, top-p=0.9 sampler and prove old-policy
  identity at receipt time.
- Run a real tactic through Reap/Lean, sign the execution evidence, and feed
  the same immutable signed envelope through CE and Online-v2 converters.
- Confirm the executor's candidate status/evidence receipts are bound to the
  Reap session. No formal training has been launched.

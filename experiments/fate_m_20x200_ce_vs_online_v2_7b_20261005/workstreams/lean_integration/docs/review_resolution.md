# Review blocker resolution

This file maps `workstreams/reviews/lean_integration_review.md` to the current
implementation. It is a local/static verification record, not a claim that the
real 7B end-to-end smoke has run.

| Review item | Resolution | Regression evidence |
|---|---|---|
| B1 real `extra.result` is a raw JSON string | `evidence._raw_openai_response` decodes at most once, validates nonempty OpenAI choices and fails closed for null/malformed/wrong types. | `WallClockTests` |
| B2 forged runner success | `runner` invokes a second process; admission requires a signed canonical v2 receipt, all frozen hashes and bound theorem/log files. Strict JSON types are used. | `EvidenceTests` |
| B3 Lean accepts `sorry` by default | Replay injects `warningAsError`, uses `--json -E hasSorry`, parses diagnostics, scans `sorry`/`admit`/all `?` holes, and has a hard timeout. | `StrictReplayTests` |
| B4 shared observer/checkpoint state | Runner strips inherited control variables and allocates per-session observer/checkpoint/state paths. ACKs are atomic and bind session/tree/step/previous/new version; coordinator receipts are fully audited. | `SessionIsolationTests`, including two concurrent sessions and cross-session forgery |
| B5 partial service failure becomes negative data | The proxy emits a terminal receipt for every success/failure. Audit requires all-success, exact model/session/tree and request counts; any failure/missing/mismatch is `indeterminate` and `negative_reward_eligible=false`. | `EvidenceTests` |

Additional hardening now shared with CE:

- Actor envelope request/statement/selected-path/state-chain hashes are checked.
- Verification uses Ed25519 over the canonical verification payload hash.
- The corresponding private key must resolve outside the workspace and, on
  POSIX, deny all group/other permissions.
- The frozen verifier-lock pins public key, exact Lean identity, Mathlib commit,
  runtime receipt, executor source/binary, manifest, command and timeout.
- Signed `cost_sha256` and `actor_envelope_sha256` prevent actor budget counters
  or other top-level fields from being changed behind a recomputed self-hash.
- A recomputed self-hash without a valid signature is rejected.

Remaining formal-run gate: the shared actor must emit exact prompt IDs, raw
completion IDs and tactic spans in an unsigned canonical v2 envelope, followed
by a real-model/real-Lean one- or two-problem smoke. Those fields cannot be
reconstructed from legacy `raw_tree.json` without loss and are deliberately not
fabricated here.

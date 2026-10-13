# Canonical actor/verifier receipt v2

Formal replay accepts only schema v2. Hashes are lowercase SHA-256 over canonical JSON (`sort_keys=true`, UTF-8, separators `,` and `:`). A field that stores the hash of its containing object is excluded while computing that hash.

Required top-level fields:

```json
{
  "schema_version": 2,
  "receipt_id": "globally unique immutable ID",
  "attempt_id": "globally unique actor attempt ID",
  "problem_id": "fate_m_003_v001",
  "statement_sha256": "...",
  "outcome": "proof | unsolved | timeout | infra_error",
  "actor_config_sha256": "...",
  "budget_config_sha256": "...",
  "tokenizer_lock_sha256": "...",
  "request": {
    "problem_id": "fate_m_003_v001",
    "statement_sha256": "...",
    "wave_index": 1,
    "actor_config_sha256": "...",
    "budget_config_sha256": "...",
    "initial_state_sha256": "..."
  },
  "request_sha256": "hash(request)",
  "selected_path": [
    {
      "step_index": 0,
      "state_before_sha256": "...",
      "state_after_sha256": "...",
      "prompt": "exact model prompt",
      "prompt_token_ids": [1, 2],
      "raw_completion_token_ids": [3, 4, 5],
      "tactic_token_span": [0, 2],
      "action": "exact tactic text sent to Lean",
      "value_target": -1.0
    }
  ],
  "verification": {
    "verifier_id": "ID pinned by trusted_verifier_lock",
    "verifier_lock_sha256": "...",
    "actor_config_sha256": "...",
    "budget_config_sha256": "...",
    "tokenizer_lock_sha256": "...",
    "request_sha256": "...",
    "statement_sha256": "...",
    "selected_path_sha256": "hash(selected_path)",
    "cost_sha256": "hash(cost)",
    "actor_envelope_sha256": "hash(top level excluding verification and receipt_sha256)",
    "initial_state_sha256": "...",
    "final_state_sha256": "...",
    "result": "verified",
    "kernel_exit_code": 0,
    "verification_receipt_sha256": "hash(verification excluding hash and signature)",
    "signature_hex": "Ed25519 signature over the 32-byte verification_receipt_sha256"
  },
  "cost": {
    "generated_tokens": 123,
    "lean_tactic_executions": 17
  },
  "receipt_sha256": "hash(full receipt excluding this field)"
}
```

Hash-domain note: `actor_config_sha256` is the canonical JSON-object identity
used by the actor, and `tokenizer_lock_sha256` is the actor's ordered tokenizer
asset-manifest identity. Neither is the raw byte SHA-256 of the corresponding
config/lock file. The frozen CE config stores those file hashes separately and
requires both domains to match before replay publication.

The frozen verifier lock must itself pin Lean 4.28.0, mathlib, executor source/binary, tactic timeout, kernel verification command and the trusted Ed25519 public key. The replay converter verifies the detached signature as well as verifier ID/lock, request/statement/path hashes, sequential state chain, terminal-state binding and kernel result. A bare `terminal_verified: true` or a self-hashed object without the verifier's private-key signature is never accepted.

The shared budget lock must define positive `max_attempts_per_problem`, `max_generated_tokens_per_problem`, and `max_lean_tactic_executions_per_problem`. These are cumulative across every receipt/attempt for the same problem, never reset per receipt. Replay publication includes sorted `per_problem_costs`, its canonical SHA-256, and the exact locked limits; the learner checks the ledger and aggregate stats again.

The frozen tokenizer is used twice: during replay admission and again during learning. `prompt_token_ids` must equal tokenization of the prompt. The tactic span must select IDs whose decode and re-encoding equal the action actually sent to Lean. There is no formal text-only fallback.

`value_target` is not actor-owned supervision. For a strictly verified, ordered,
continuous selected path of length `L`, step `i` must carry exactly
`-(L-i)`: `[-L, ..., -2, -1]`. The trusted verifier and CE converter derive
this value independently and treat the serialized field only as a redundant,
signed consistency check. Paths longer than 64 tactics and any wrong, missing,
reversed, duplicate, non-integral, or non-contiguous step index are rejected;
targets are never clamped into the 64 bins.

The replay transition retains the exact canonical `selected_path_step`, the
common `selected_path_sha256`, and the full detached verifier object. The
trainer reconstructs the ordered path, re-verifies the Ed25519 signature using
the frozen verifier key/lock, and re-derives transition IDs. This is required:
the replay manifest's own hash is only an integrity checksum, not an
authentication boundary, so a JSONL row deletion plus recomputed manifest
self-hashes must still fail against the immutable signed path commitment.
The trainer reads the verifier lock as one byte string, checks its SHA-256
against the frozen config before parsing the public key, and repeats this
verified read at the point of use. A substituted lock plus attacker-signed path
therefore fails even if every replay self-hash is recomputed.

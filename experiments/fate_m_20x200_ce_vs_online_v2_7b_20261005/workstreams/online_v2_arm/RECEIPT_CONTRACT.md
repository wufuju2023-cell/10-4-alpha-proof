# Online-v2 receipt-to-sample contract

`build_rollout_samples` is the only supported boundary from actor/search output
to `RolloutSample`. It returns a `ValidatedRolloutWave` sequence that cannot be
constructed through its public API and carries a digest over every sample field
and tensor. The learner calls `wave.validate()` immediately before use, so
post-build tensor or metadata mutation is detected. Callers must not deserialize
arbitrary JSON directly into the learner schema.

The boundary consumes three frozen, canonical-SHA-256 receipts:

1. `SearchStateReceipt` contains exactly one complete same-state candidate set.
   It pins `wave_id`, behavior `policy_version`, behavior adapter version/SHA-256,
   base model version/live-state SHA-256, tokenizer SHA-256, prompt token ids,
   EOS id/convention, expected raw-row count, complete token arrays, exact
   warped `q_old`, audit-only unwarped `p_old`, raw-to-Lean execution mapping,
   per-row finish reason, multiplicity, and Q estimates.
2. `VerifierReceipt` independently binds one search event to its strict Lean
   result and pins both the search receipt ID and its canonical SHA-256.
   Verifier coverage must exactly equal the candidate-event set.
3. `ProofPathReceipt` lists one unique, continuous root-to-terminal event chain
   and binds its final event to both the ID and SHA-256 of a strict
   verified-proof/disproof receipt.  It also pins a canonical digest over the
   ordered `(event, search ID/hash, verifier ID/hash)` chain.

The builder rejects mixed policy versions, wave ids, tokenizer/EOS contracts,
partial candidate sets, duplicate event ids, verifier coverage gaps, broken
path ancestry, unaccounted terminal results, cross-receipt hash-chain drift,
and receipt content-hash drift.  Reissuing changed search or verifier content
under an existing ID therefore cannot reuse downstream verifier/path receipts.
It then derives—not accepts—local reward, draw-multiplicity-weighted same-state
baseline, standardized/clipped advantage, verified-path membership, and
remaining-step value distance.

The shared CE64 semantics are exact: an ordered verified path of length `L`
derives positive internal `value_distance=L-i` for zero-based event position
`i`, equivalent to canonical receipt `value_target=-(L-i)`. No actor-provided
target is consumed. A proof-path receipt with more than 64 events is rejected,
never clipped into the value-head range.

Every non-terminal event on that path must have verifier status `unresolved`,
the protocol's explicit “tactic applied and search continues” state. An
`invalid_tactic`, `timeout`, or `infrastructure_error` event cannot be included
in a verified path. This is checked both while constructing the wave and again
at the pre-learner validation boundary. The low-level two-hot encoder also
rejects values outside `[1,64]` rather than silently clamping them.

For the single-backbone PEFT path, the learner also passes the wave's frozen
behavior/base identity to `SingleBackboneAdapterReferences.validate_wave_identity`
before any forward or optimizer mutation. The backend must expose a live base
identity getter; a cached model receipt alone is intentionally rejected.

Every resulting sample contains mandatory `wave_id` and `event_id`.  A durable
consumer ledger can therefore key an accepted update by globally unique
`wave_id` and make every event within it idempotent by `event_id`.

## Token boundary

`prompt_len` is the exact length of `prompt_token_ids`. The attention mask must
be one contiguous prefix. For raw-action receipts, the action mask is exactly
the complete attended suffix `[prompt_len, attended_len)`; it cannot select
padding, omit a generated token, or exclude EOS after a stop. A length stop has
no EOS and retains every generated token. Tactic spans are separately bound as
execution metadata to the source Lean event; they do not control the PPO action
mask. `q_old` is verified by recomputing the same temperature-then-top-p warp at
point of use. `p_old` remains audit-only. With `included_terminal`, exactly one
EOS must occur as the last action token. With `excluded`, EOS may not occur in
the action. The state receipt uses `per_candidate` when a request contains a
mix of stop and length rows; validation derives the row's EOS convention from
its signed finish reason.

These checks occur both while validating the immutable search receipt and on
the constructed `RolloutSample`.

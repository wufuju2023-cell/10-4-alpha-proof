# Trusted verifier lock v1

The verifier lock is immutable run input. Its file SHA-256 is pinned by both
arms. The Ed25519 private key is never stored below the workspace; only its
raw 32-byte public key is present here.

```json
{
  "schema_version": 1,
  "verifier_id": "lean428-kernel-...",
  "ed25519_public_key_hex": "64 lowercase hex characters",
  "lean_toolchain": "leanprover/lean4:v4.28.0",
  "lean_version": "exact `lean --version` output",
  "lean_git_hash": "40 lowercase hex characters",
  "mathlib_revision": "40 lowercase hex characters",
  "executor_source_sha256": "sha256(strict_replay.py)",
  "executor_binary_sha256": "sha256(the pinned lake executable)",
  "runtime_receipt_sha256": "sha256(runtime_receipt.json)",
  "lake_manifest_sha256": "sha256(course-project/lake-manifest.json)",
  "kernel_command": ["{lake}", "env", "lean", "--json", "-E", "hasSorry", "{theorem}"],
  "tactic_timeout_seconds": 120,
  "forbidden_tokens": ["sorry", "admit", "holes"]
}
```

`strict_replay.py` rejects a lock whose file hash, runtime/manifest/executable
pins, source hash, exact Lean identity, Mathlib revision, command, timeout, or
public key do not match. It accepts an unsigned `canonical_search_receipt_v2`
actor envelope, validates the request/statement/path/state chain, compiles the
materialized theorem in an independent process, writes `kernel_receipt.json`,
then signs the verification payload exactly as `ce_arm/docs/receipt_v2.md`
requires. `receipt.json` is the resulting canonical signed receipt.

The signature is Ed25519 over the 32 raw bytes represented by
`verification_receipt_sha256`. That hash is SHA-256 of canonical JSON for the
`verification` object after removing `verification_receipt_sha256` and
`signature_hex`. The signed payload also includes `cost_sha256` and
`actor_envelope_sha256` (the canonical top-level receipt excluding
`verification`/`receipt_sha256`), so actor budget counters cannot be changed by
recomputing only the untrusted top-level self-hash.

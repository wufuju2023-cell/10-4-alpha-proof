# PF-1 cross-receipt hash-chain remediation

Status: complete (2026-10-05).

## Change

- `VerifierReceipt` now includes `search_receipt_sha256`; the builder requires
  both search receipt ID and content hash to match the event's actual search.
- `ProofPathReceipt` now includes `terminal_verifier_receipt_sha256` and
  `ordered_event_chain_sha256`.
- The ordered chain digest covers each path position's event ID, search receipt
  ID/hash, and verifier receipt ID/hash. The builder reconstructs it from its
  validated inputs and requires an exact match.
- The terminal path check now matches both verifier ID and verifier content
  hash before accepting proof/disproof credit.

This closes the same-ID substitution gap: changing and re-hashing a search
receipt while retaining its old ID no longer permits reuse of the old verifier
or proof-path authorization.

## Regression evidence

`tests/test_receipt_builder.py::test_same_id_search_replacement_cannot_reuse_old_verifier_or_path`
first validates the original chain, then creates different search content with
the same receipt ID and reuses the old verifier and path. The builder rejects
it at the search-to-verifier hash link.

Targeted command:

```text
python -m pytest -q tests/test_receipt_builder.py scripts/smoke.py
```

Result: `10 passed`.

The first concurrent full-suite run reached `35 passed, 7 failed`; all seven
failures were in concurrently modified adapter/ledger code and none involved
the receipt builder. No learner, ledger, or adapter file was modified for this
remediation.

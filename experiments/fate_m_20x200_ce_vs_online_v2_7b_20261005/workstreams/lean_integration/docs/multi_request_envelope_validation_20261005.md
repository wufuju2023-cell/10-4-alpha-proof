# Multi-request / multi-state canonical envelope validation

Recorded: `2026-10-05`
Scope: local source and CPU tests only. No remote runtime or GPU was started.

## Result

The formal join no longer assumes one policy request or one search state.
`candidate_facts.py` now publishes
`fate.reap.canonical_candidate_facts.v2` from one or more repeated
`--actor-receipt` inputs. Each committed request is bound by `request_id` and
file SHA-256 to exactly one canonical search state. Every state independently
must partition all 64 raw samples, while the selected proof path may cross
requests/states.

Before private-key access, `e2e_receipt_join.py`:

1. verifies the report binds the exact set of policy receipt file hashes;
2. validates every receipt's canonical self-hash and generation commitment;
3. reconstructs v2 candidate facts from all producer artifacts;
4. checks request/state uniqueness plus the cross-state parent, depth, and
   successor-state hash chain;
5. requires exact equality with the supplied sidecar;
6. builds one `SearchStateEvidence` per policy request and passes all states to
   the existing canonical-v2 shared actor envelope;
7. rechecks every producer hash immediately before signing.

## Focused regressions

- A two-request/two-state selected path reconstructs and audits as ready.
- The two states retain separate 0..63 raw-sample partitions (128 total raw
  samples).
- Reusing one policy request for a distinct state fails closed.
- Existing executor-hash/state-hash tamper tests still reject before any
  private-key read.

Validation command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$base/lean_integration/src;$base/policy_service_bridge/src;$base/shared_actor_bridge/src"
python -m unittest discover -s "$base/lean_integration/tests" -v
```

Result: `Ran 58 tests` / `OK`.

## Remaining boundary

This is a local canonical-envelope implementation result, not a completed
formal run. A live BestFirstSearch producer has not yet emitted and signed a
multi-request trajectory through strict replay and both learner converters.
The current Reap producer still rejects branching AND proof paths; v2 supports
linear selected paths across multiple request/state expansions, up to the
canonical envelope's frozen 64-step horizon.

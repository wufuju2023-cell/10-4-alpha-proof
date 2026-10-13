# Candidate-facts pre-sign reconstruction validation

Recorded: `2026-10-05T17:40:48+08:00`

Scope: local Python validation only. No remote runtime was started, no GPU was
used, and `integration_bundle` was not modified.

## Interface change

`fate_reap.candidate_facts.rebuild_candidate_facts(...)` is the side-effect-free
producer-artifact reconstruction interface. It accepts the canonical observer,
raw tree, terminal `result.json`, committed actor receipt, session/tree IDs, and
the frozen raw count. `collect_candidate_facts(...)` now calls this function and
only adds exclusive atomic publication.

`audit_run(...)` and `run_join(...)` independently call the reconstruction
interface. They require exact equality with the supplied sidecar. `run_join`
builds the envelope from the in-memory reconstruction and rechecks the four raw
input hashes plus the sidecar file hash immediately before private-key access.

## Validation commands and results

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$lean/src;$policy/src;$shared/src"
python -m unittest discover -s "$lean/tests" -v
```

Result: `Ran 46 tests in 0.714s` / `OK`.

```powershell
python -m pytest -p no:cacheprovider `
  "$policy/tests/test_service.py" `
  "$lean/tests/test_candidate_facts.py" `
  "$lean/tests/test_e2e_receipt_join.py" -q
```

Result: `33 passed in 1.24s`.

The targeted regressions rewrite either `executor_receipt_sha256` or
`state_after_sha256`, recompute the sidecar self-hash, call `run_join`, and
assert rejection before `_load_private_key` is called.

## Validated source hashes

- `src/fate_reap/candidate_facts.py`:
  `71911e69d16d03761c6b5ea70b5395025bfe444e4867509ab49f4a5109047328`
- `src/fate_reap/e2e_receipt_join.py`:
  `ec7afacd3f587c36a4948d8a96924bb9dee63bad16bee013eb1d5db400fd72c8`
- `tests/test_e2e_receipt_join.py`:
  `a71d9b63eb38eb91ba4cfd27ad9e15bb82705faffe003f42e19494a798e3713d`

No `__pycache__` directories remained under the Lean-integration workstream.

## Deliberate fail-closed limits

- Only one committed policy request is accepted by this smoke join. Formal
  multi-request/multi-state reconstruction remains unimplemented.
- Branching AND proofs remain rejected by the runtime because the v1 sidecar
  only represents a linear selected path.

# Verification

- Local command: `python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests`
- Result after official prompt bridge and representative capture entrypoint: 24 passed (Windows, Python 3.13.7)
- Cross-boundary command: `python -m pytest -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/shared_actor_bridge/tests experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests`
- Result after official prompt bridge and representative capture entrypoint: 40 passed
- Linux portability correction: the session-concurrency test now selects
  receipts with `path.parent.parent.name == "s1"` instead of searching for the
  Windows-only `"\\s1\\"` separator substring. After this correction the local
  targeted policy suite is 24 passed and the isolated-import policy + shared
  actor suite is 40 passed. The earlier remote `[] != [1, 2]` report was a test
  path-selection defect, not a service/locking failure; the corrected assertion
  still verifies request sequences `[1, 2]` for exactly the `s1` receipt parent.
- Coverage includes the evolved `RawGeneration` request/sample identity, full
  sampling and unwarped token log-probs, generation receipt commitment,
  active adapter checks, tokenizer/config pins, and a non-reentrant shared-lock
  regression through the helper's nested transaction boundary.
- Bytecode compile: `python -m compileall -q experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/src experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/workstreams/policy_service_bridge/tests`
- Result: PASS
- Real pinned Reap parser check (remote ephemeral runtime):
  `lake env lean --run tests/ReapResponseCompat.lean tests/reap_response_fixture.json`
- Result: `REAP_OPENAI_RESPONSE_COMPAT_PASS choices=2 token_logprobs=2,2`
- New real E2E entrypoint syntax check:
  `python -m py_compile workstreams/policy_service_bridge/scripts/real_reap_e2e_smoke.py`
- Result: PASS (Windows, Python 3.13.7)
- Shared actor + policy service regression suites after adding the entrypoint:
  `python -m pytest -q workstreams/policy_service_bridge/tests workstreams/shared_actor_bridge/tests`
- Result after official prompt bridge and representative capture entrypoint: 40 passed

- Full policy-service + shared-actor + Lean-integration command (using
  `--import-mode=importlib` because two suites contain a top-level
  `test_bridge.py`): 84 passed.
- The immutable prior real-E2E `fate.policy_request.v2` receipt was loaded and
  validated through the updated reader (using the Windows extended-length path
  prefix only to cross `MAX_PATH`): PASS. The evidence file was not modified.

The corrected real E2E has not yet been rerun on the prepared GPU/Lean host.
The prior run remains immutable mismatch evidence and was not edited. The rerun
must show that the actual incoming Reap root state is transformed by the pinned
PromptManage implementation and that the v2 receipt prompt hash/token IDs equal
the captured real actor call. This Windows checkout has neither the pinned 7B
weights nor the prepared Lean 4.28 runtime; that is the exact execution blocker.

The CPU-only 12-task root-state capture entrypoint is
`scripts/capture_representative_reap_prompts.py`. `py_compile` and `--help`
both pass. Its six focused tests cover the exact frozen task set, plan-relative
data-path priority, one-step
search options, point-of-use Reap-envelope parsing, 64 strict-provenance
`skip` choices, duplicate/noncanonical fail-closed behavior, complete-manifest
admission and absence of Torch/Transformers/PEFT or synthetic root-state code.
It has not been run here because this checkout does not contain the prepared
pinned Reap/Lean runtime. The remote command and output contract are recorded
in `REPRESENTATIVE_SMOKE_PLAN.md`.

Every OpenAI choice now includes `raw_sample_index` and
`service_candidate_sha256`, checked by the Python receipt tests. The parser
fixture includes the fields but still needs rerunning in the prepared remote
Lean runtime. No execution tactic token span is emitted because Reap's later
normalization/deduplication result is not returned to this service; inventing a
span here would weaken the full-completion PPO action contract.

Audited contracts:

- pinned Reap `Tactic/Generator.lean` builds one-user-message OpenAI Chat
  requests and sums `choices[].logprobs.content[].logprob`;
- `lean_integration/service_receipt_proxy.py` records only terminal HTTP
  hashes/counts and cannot supply raw token evidence;
- `shared_actor_bridge/hf_adapter.py` already implements exact raw-ID capture
  and both unwarped behavior and warped sampling rescoring. The service passes
  through its request id, active behavior/base/tokenizer identity and the
  caller's already-held transaction boundary;
- target `scripts/cloud_real_smoke.py` loads the correct live objects but still
  uses `run_mock_search`, so it is not a real search/Lean E2E.

No model was downloaded or loaded and no training was started by this
workstream. No Lean state, tactic span, search value or verifier result is
claimed by its tests. The generation receipt has an immutable SHA-256
commitment; cryptographic Reap signing occurs downstream on the complete
canonical envelope after actual Lean/search facts are joined.

The one-session real E2E command and remaining signed-envelope join gate are
documented in `README.md` under “One-session real E2E smoke”. This Windows
checkout does not contain the remote GPU model, formal adapter, curriculum
JSONL, or `/tmp/fate-m-reap428/runtime`, so no real model or Lean run is claimed
by local verification.

from __future__ import annotations

from dataclasses import replace
import math

import pytest

from alphaproof_online_v2_arm.builder import (
    ValidatedRolloutWave,
    build_rollout_samples,
    validate_training_wave,
)
from alphaproof_online_v2_arm.receipts import (
    CandidateReceipt,
    ProofPathReceipt,
    ReceiptInvariantError,
    SearchStateReceipt,
    VerifierReceipt,
    ordered_proof_chain_sha256,
)


TOKENIZER_SHA = "a" * 64


def candidate(
    event_id: str,
    action: str,
    *,
    value: float,
    sample_multiplicity: int,
    depth: int = 0,
    parent_event_id: str | None = None,
    trajectory_id: str | None = None,
) -> CandidateReceipt:
    return CandidateReceipt(
        event_id=event_id,
        trajectory_id=trajectory_id or f"trajectory-{event_id}",
        action=action,
        depth=depth,
        parent_event_id=parent_event_id,
        input_ids=(1, 2, 3, 10),
        attention_mask=(1, 1, 1, 1),
        action_mask=(False, False, True, True),
        old_logprobs=(0.0, 0.0, -0.2, -0.3),
        action_value=value,
        sample_multiplicity=sample_multiplicity,
    )


def search(*candidates: CandidateReceipt, receipt_id: str = "search-s0",
           state_id: str = "s0", tokenizer_sha256: str = TOKENIZER_SHA) -> SearchStateReceipt:
    return SearchStateReceipt.create(
        receipt_id=receipt_id,
        problem_id="p0",
        wave_id="wave-0001",
        policy_version="policy-0001",
        behavior_version="policy-0001",
        behavior_sha256="b" * 64,
        base_version="real-prover-test",
        base_sha256="c" * 64,
        state_id=state_id,
        tokenizer_sha256=tokenizer_sha256,
        eos_token_id=10,
        eos_convention="included_terminal",
        prompt_token_ids=(1, 2),
        candidate_set_complete=True,
        expected_candidate_count=len(candidates),
        candidates=tuple(candidates),
    )


def verifier(event_id: str, search_receipt: SearchStateReceipt, status: str) -> VerifierReceipt:
    return VerifierReceipt.create(
        receipt_id=f"verifier-{event_id}",
        problem_id="p0",
        wave_id="wave-0001",
        event_id=event_id,
        search_receipt_id=search_receipt.receipt_id,
        search_receipt_sha256=search_receipt.receipt_sha256,
        status=status,
        terminal_verified=status in {"verified_proof", "verified_disproof"},
    )


def proof_path(
    *links: tuple[str, SearchStateReceipt, VerifierReceipt],
) -> ProofPathReceipt:
    event_ids = tuple(event_id for event_id, _, _ in links)
    terminal = links[-1][2]
    chain = tuple(
        (
            event_id,
            state.receipt_id,
            state.receipt_sha256,
            event_verifier.receipt_id,
            event_verifier.receipt_sha256,
        )
        for event_id, state, event_verifier in links
    )
    return ProofPathReceipt.create(
        receipt_id="path-p0",
        problem_id="p0",
        wave_id="wave-0001",
        outcome="proof",
        terminal_verifier_receipt_id=terminal.receipt_id,
        terminal_verifier_receipt_sha256=terminal.receipt_sha256,
        ordered_event_chain_sha256=ordered_proof_chain_sha256(chain),
        event_ids=event_ids,
    )


def test_builder_derives_targets_and_provenance_from_complete_receipts() -> None:
    state = search(
        # The search producer recorded its pre-execution/default Q for both
        # actions.  Strict verifier outcomes must supply the terminal returns.
        candidate("e-good", "exact h", value=0.0, sample_multiplicity=1),
        candidate("e-bad", "rfl", value=0.0, sample_multiplicity=3),
    )
    good_verifier = verifier("e-good", state, "verified_proof")
    bad_verifier = verifier("e-bad", state, "invalid_tactic")
    samples = build_rollout_samples(
        [state],
        [good_verifier, bad_verifier],
        [proof_path(("e-good", state, good_verifier))],
    )
    by_event = {sample.event_id: sample for sample in samples}
    good, bad = by_event["e-good"], by_event["e-bad"]
    assert good.wave_id == "wave-0001"
    assert good.prompt_len == 2
    assert good.search_receipt_sha256 == state.receipt_sha256
    assert good.action_value == 1.0
    assert bad.action_value == -0.1
    assert good.baseline_value == pytest.approx(0.175)
    assert good.advantage == pytest.approx(0.825 / (math.sqrt(0.226875) + 1e-6))
    assert bad.baseline_value == pytest.approx(0.175)
    assert bad.advantage == pytest.approx(-0.275 / (math.sqrt(0.226875) + 1e-6))
    assert good.local_reward == 1.0
    assert good.on_verified_solution_path and good.value_distance == 1.0
    assert good.proof_path_receipt_id == "path-p0"
    assert bad.local_reward == -0.1
    assert not bad.on_verified_solution_path and bad.value_distance is None
    assert isinstance(samples, ValidatedRolloutWave)
    assert samples.validate() == tuple(samples)


def test_per_candidate_eos_allows_mixed_stop_and_length_raw_actions() -> None:
    stop = CandidateReceipt(
        event_id="raw-stop", trajectory_id="trajectory-raw", action="raw:stop",
        depth=0, parent_event_id=None, input_ids=(1, 2, 3, 10),
        attention_mask=(1, 1, 1, 1), action_mask=(False, False, True, True),
        old_logprobs=(0.0, 0.0, -0.2, -0.3), action_value=0.0,
        sample_multiplicity=1, execution_event_id="exec-stop", execution_action="simp",
        execution_action_token_ids=(3,),
        raw_sample_indices=(0,), tactic_token_spans=((0, 1),), finish_reason="stop",
        unwarped_old_logprobs=(0.0, 0.0, -0.4, -0.5),
    )
    length = CandidateReceipt(
        event_id="raw-length", trajectory_id="trajectory-raw", action="raw:length",
        depth=0, parent_event_id=None, input_ids=(1, 2, 4, 5),
        attention_mask=(1, 1, 1, 1), action_mask=(False, False, True, True),
        old_logprobs=(0.0, 0.0, -0.6, -0.7), action_value=1.0,
        sample_multiplicity=1, execution_event_id="exec-length", execution_action="ring",
        execution_action_token_ids=(4,),
        raw_sample_indices=(1,), tactic_token_spans=((0, 1),), finish_reason="length",
        unwarped_old_logprobs=(0.0, 0.0, -0.8, -0.9),
    )
    state = SearchStateReceipt.create(
        receipt_id="search-mixed-eos", problem_id="p0", wave_id="wave-0001",
        policy_version="policy-0001", behavior_version="policy-0001",
        behavior_sha256="b" * 64, base_version="real-prover-test", base_sha256="c" * 64,
        state_id="s-mixed-eos", tokenizer_sha256=TOKENIZER_SHA, eos_token_id=10,
        eos_convention="per_candidate", prompt_token_ids=(1, 2),
        candidate_set_complete=True, expected_candidate_count=2, candidates=(stop, length),
    )
    verifiers = [
        VerifierReceipt.create(
            receipt_id="v-stop", problem_id="p0", wave_id="wave-0001", event_id="raw-stop",
            search_receipt_id=state.receipt_id, search_receipt_sha256=state.receipt_sha256,
            status="invalid_tactic", terminal_verified=False, execution_event_id="exec-stop",
        ),
        VerifierReceipt.create(
            receipt_id="v-length", problem_id="p0", wave_id="wave-0001", event_id="raw-length",
            search_receipt_id=state.receipt_id, search_receipt_sha256=state.receipt_sha256,
            status="unresolved", terminal_verified=False, execution_event_id="exec-length",
        ),
    ]
    samples = build_rollout_samples([state], verifiers)
    by_event = {item.event_id: item for item in samples}
    assert by_event["raw-stop"].eos_convention == "included_terminal"
    assert by_event["raw-length"].eos_convention == "excluded"
    assert by_event["raw-stop"].action_mask.tolist() == [False, False, True, True]
    assert by_event["raw-length"].action_mask.tolist() == [False, False, True, True]
    wrong_binding = VerifierReceipt.create(
        receipt_id="v-stop-wrong-execution", problem_id="p0", wave_id="wave-0001",
        event_id="raw-stop", search_receipt_id=state.receipt_id,
        search_receipt_sha256=state.receipt_sha256, status="invalid_tactic",
        terminal_verified=False, execution_event_id="wrong-execution",
    )
    with pytest.raises(ReceiptInvariantError, match="shared Lean execution event"):
        build_rollout_samples([state], [wrong_binding, verifiers[1]])
    malformed = replace(stop, action_mask=(False, False, True, False))
    bad_state = replace(
        state, receipt_sha256="", candidates=(malformed, length),
        expected_candidate_count=2,
    )
    bad_state = SearchStateReceipt.create(**{
        field: getattr(bad_state, field)
        for field in bad_state.__dataclass_fields__ if field != "receipt_sha256"
    })
    with pytest.raises(ReceiptInvariantError, match="complete completion"):
        bad_state.validate()


def test_proof_path_cannot_reuse_one_lean_execution_via_sibling_raw_rows() -> None:
    first = CandidateReceipt(
        event_id="raw-sibling-1", trajectory_id="trajectory-shared", action="raw:1",
        depth=0, parent_event_id=None, input_ids=(1, 2, 3, 10),
        attention_mask=(1, 1, 1, 1), action_mask=(False, False, True, True),
        old_logprobs=(0.0, 0.0, -0.2, -0.3), action_value=1.0,
        sample_multiplicity=1, execution_event_id="exec-shared", execution_action="simp",
        execution_action_token_ids=(3,),
        raw_sample_indices=(0,), tactic_token_spans=((0, 1),), finish_reason="stop",
        unwarped_old_logprobs=(0.0, 0.0, -0.4, -0.5),
    )
    sibling = replace(
        first, event_id="raw-sibling-2", action="raw:2", input_ids=(1, 2, 4, 10),
        raw_sample_indices=(1,), execution_action_token_ids=(4,),
    )
    state = SearchStateReceipt.create(
        receipt_id="search-shared-execution", problem_id="p0", wave_id="wave-0001",
        policy_version="policy-0001", behavior_version="policy-0001",
        behavior_sha256="b" * 64, base_version="real-prover-test", base_sha256="c" * 64,
        state_id="s-shared-execution", tokenizer_sha256=TOKENIZER_SHA, eos_token_id=10,
        eos_convention="per_candidate", prompt_token_ids=(1, 2),
        candidate_set_complete=True, expected_candidate_count=2, candidates=(first, sibling),
    )
    verifiers = [
        VerifierReceipt.create(
            receipt_id=f"v-{candidate.event_id}", problem_id="p0", wave_id="wave-0001",
            event_id=candidate.event_id, search_receipt_id=state.receipt_id,
            search_receipt_sha256=state.receipt_sha256, status="verified_proof",
            terminal_verified=True, execution_event_id="exec-shared",
        ) for candidate in (first, sibling)
    ]
    paths = [
        proof_path((candidate.event_id, state, verifier))
        for candidate, verifier in zip((first, sibling), verifiers, strict=True)
    ]
    paths[1] = ProofPathReceipt.create(
        receipt_id="path-sibling", problem_id=paths[1].problem_id,
        wave_id=paths[1].wave_id, outcome=paths[1].outcome,
        terminal_verifier_receipt_id=paths[1].terminal_verifier_receipt_id,
        terminal_verifier_receipt_sha256=paths[1].terminal_verifier_receipt_sha256,
        ordered_event_chain_sha256=paths[1].ordered_event_chain_sha256,
        event_ids=paths[1].event_ids,
    )
    with pytest.raises(ReceiptInvariantError, match="reuses a Lean execution"):
        build_rollout_samples([state], verifiers, paths)


def test_65_step_verified_path_is_rejected_not_clamped() -> None:
    path = ProofPathReceipt.create(
        receipt_id="path-too-long",
        problem_id="p0",
        wave_id="wave-0001",
        outcome="proof",
        terminal_verifier_receipt_id="terminal-v",
        terminal_verifier_receipt_sha256="d" * 64,
        ordered_event_chain_sha256="e" * 64,
        event_ids=tuple(f"event-{index}" for index in range(65)),
    )
    with pytest.raises(ReceiptInvariantError, match="64-bin value horizon"):
        path.validate()


def test_validated_wave_is_not_publicly_constructible_and_detects_mutation() -> None:
    state = search(candidate("e0", "exact h", value=0.0, sample_multiplicity=1))
    wave = build_rollout_samples(
        [state], [verifier("e0", state, "unresolved")]
    )
    with pytest.raises(TypeError, match="only be constructed"):
        ValidatedRolloutWave(tuple(wave), _token=object())
    wave[0].input_ids[2] = 4
    with pytest.raises(ReceiptInvariantError, match="content changed"):
        wave.validate()


def test_prelearner_validation_recomputes_advantage() -> None:
    state = search(candidate("e0", "exact h", value=0.0, sample_multiplicity=1))
    wave = build_rollout_samples(
        [state], [verifier("e0", state, "unresolved")]
    )
    wave[0].advantage = 5.0
    with pytest.raises(ReceiptInvariantError, match="advantage mismatch"):
        validate_training_wave(wave)


def test_tampered_search_receipt_is_rejected() -> None:
    state = search(candidate("e0", "exact h", value=1.0, sample_multiplicity=1))
    tampered = replace(state, expected_candidate_count=2)
    with pytest.raises(ReceiptInvariantError, match="candidate count|hash mismatch"):
        build_rollout_samples([tampered], [verifier("e0", state, "unresolved")])


def test_same_id_search_replacement_cannot_reuse_old_verifier_or_path() -> None:
    """Reviewer PF-1: IDs alone must not authorize a substituted search."""

    original = search(candidate("e0", "exact h", value=1.0, sample_multiplicity=1))
    old_verifier = verifier("e0", original, "verified_proof")
    old_path = proof_path(("e0", original, old_verifier))
    build_rollout_samples([original], [old_verifier], [old_path])

    replacement = search(
        candidate("e0", "exact h", value=-0.75, sample_multiplicity=1),
        receipt_id=original.receipt_id,
        state_id=original.state_id,
    )
    assert replacement.receipt_id == original.receipt_id
    assert replacement.receipt_sha256 != original.receipt_sha256
    with pytest.raises(ReceiptInvariantError, match="not bound to its search event"):
        build_rollout_samples([replacement], [old_verifier], [old_path])


def test_partial_candidate_set_cannot_define_advantage() -> None:
    state = search(candidate("e0", "exact h", value=1.0, sample_multiplicity=1))
    partial = replace(state, candidate_set_complete=False)
    partial = replace(partial, receipt_sha256=SearchStateReceipt.create(
        receipt_id=partial.receipt_id,
        problem_id=partial.problem_id,
        wave_id=partial.wave_id,
        policy_version=partial.policy_version,
        behavior_version=partial.behavior_version,
        behavior_sha256=partial.behavior_sha256,
        base_version=partial.base_version,
        base_sha256=partial.base_sha256,
        state_id=partial.state_id,
        tokenizer_sha256=partial.tokenizer_sha256,
        eos_token_id=partial.eos_token_id,
        eos_convention=partial.eos_convention,
        prompt_token_ids=partial.prompt_token_ids,
        candidate_set_complete=False,
        expected_candidate_count=partial.expected_candidate_count,
        candidates=partial.candidates,
    ).receipt_sha256)
    with pytest.raises(ReceiptInvariantError, match="partial candidate set"):
        build_rollout_samples([partial], [verifier("e0", state, "unresolved")])


def test_noncontinuous_action_suffix_is_rejected() -> None:
    broken = replace(
        candidate("e0", "exact h", value=1.0, sample_multiplicity=1),
        action_mask=(False, True, False, True),
    )
    state = search(broken)
    with pytest.raises(ReceiptInvariantError, match="attended non-prompt|contiguous tactic span"):
        build_rollout_samples([state], [verifier("e0", state, "unresolved")])


def test_tokenizer_and_eos_contract_may_not_drift_within_wave() -> None:
    first = search(
        candidate("e0", "exact h", value=1.0, sample_multiplicity=1),
        receipt_id="search-s0",
        state_id="s0",
    )
    second = search(
        candidate("e1", "exact h", value=1.0, sample_multiplicity=1),
        receipt_id="search-s1",
        state_id="s1",
        tokenizer_sha256="b" * 64,
    )
    with pytest.raises(ReceiptInvariantError, match="tokenizer/EOS"):
        build_rollout_samples(
            [first, second],
            [verifier("e0", first, "unresolved"), verifier("e1", second, "unresolved")],
        )


def test_behavior_or_base_identity_may_not_drift_within_wave() -> None:
    first = search(
        candidate("e0", "exact h", value=1.0, sample_multiplicity=1),
        receipt_id="search-s0",
        state_id="s0",
    )
    second = search(
        candidate("e1", "exact h", value=1.0, sample_multiplicity=1),
        receipt_id="search-s1",
        state_id="s1",
    )
    second = SearchStateReceipt.create(
        **{
            name: getattr(second, name)
            for name in second.__dataclass_fields__
            if name not in {"receipt_sha256", "base_sha256"}
        },
        base_sha256="d" * 64,
    )
    with pytest.raises(ReceiptInvariantError, match="behavior/base identities"):
        build_rollout_samples(
            [first, second],
            [
                verifier("e0", first, "unresolved"),
                verifier("e1", second, "unresolved"),
            ],
        )


def test_verifier_coverage_is_exact_and_event_ids_are_wave_unique() -> None:
    first = search(
        candidate("same-event", "exact h", value=1.0, sample_multiplicity=1),
        receipt_id="search-s0",
        state_id="s0",
    )
    second = search(
        candidate("same-event", "rfl", value=0.0, sample_multiplicity=1),
        receipt_id="search-s1",
        state_id="s1",
    )
    with pytest.raises(ReceiptInvariantError, match="duplicate event_id across wave"):
        build_rollout_samples(
            [first, second],
            [verifier("same-event", first, "unresolved")],
        )


def test_terminal_verifier_requires_a_valid_continuous_proof_path() -> None:
    root = candidate(
        "e-root", "intro h", value=0.5, sample_multiplicity=1,
        trajectory_id="trajectory-proof",
    )
    terminal = candidate(
        "e-terminal", "exact h", value=1.0, sample_multiplicity=1,
        depth=1, parent_event_id="wrong-parent", trajectory_id="trajectory-proof",
    )
    s0 = search(root, receipt_id="search-s0", state_id="s0")
    s1 = search(terminal, receipt_id="search-s1", state_id="s1")
    verifiers = [
        verifier("e-root", s0, "unresolved"),
        verifier("e-terminal", s1, "verified_proof"),
    ]
    with pytest.raises(ReceiptInvariantError, match="continuous root-to-terminal"):
        build_rollout_samples(
            [s0, s1], verifiers,
            [proof_path(("e-root", s0, verifiers[0]), ("e-terminal", s1, verifiers[1]))],
        )
    with pytest.raises(ReceiptInvariantError, match="every terminal verifier"):
        build_rollout_samples([s0, s1], verifiers, [])


def _two_step_verified_path(nonterminal_status: str):
    root = candidate(
        "e-root", "intro h", value=0.5, sample_multiplicity=1,
        trajectory_id="trajectory-proof",
    )
    terminal = candidate(
        "e-terminal", "exact h", value=1.0, sample_multiplicity=1,
        depth=1, parent_event_id="e-root", trajectory_id="trajectory-proof",
    )
    s0 = search(root, receipt_id="search-s0", state_id="s0")
    s1 = search(terminal, receipt_id="search-s1", state_id="s1")
    verifiers = [
        verifier("e-root", s0, nonterminal_status),
        verifier("e-terminal", s1, "verified_proof"),
    ]
    return s0, s1, verifiers, proof_path(
        ("e-root", s0, verifiers[0]), ("e-terminal", s1, verifiers[1])
    )


def test_multistep_value_distance_requires_valid_nonterminal_states() -> None:
    s0, s1, verifiers, path = _two_step_verified_path("unresolved")
    wave = build_rollout_samples([s0, s1], verifiers, [path])
    assert {sample.event_id: sample.value_distance for sample in wave} == {
        "e-root": 2.0,
        "e-terminal": 1.0,
    }


@pytest.mark.parametrize("status", ["invalid_tactic", "timeout", "infrastructure_error"])
def test_failed_nonterminal_event_cannot_receive_verified_path_value(status: str) -> None:
    s0, s1, verifiers, path = _two_step_verified_path(status)
    with pytest.raises(ReceiptInvariantError, match="successfully applied unresolved state"):
        build_rollout_samples([s0, s1], verifiers, [path])


@pytest.mark.parametrize(
    ("status", "reward"),
    [("invalid_tactic", -0.1), ("timeout", 0.0), ("infrastructure_error", 0.0)],
)
def test_prelearner_rejects_failed_nonterminal_in_verified_path(
    status: str, reward: float
) -> None:
    s0, s1, verifiers, path = _two_step_verified_path("unresolved")
    wave = build_rollout_samples([s0, s1], verifiers, [path])
    root = next(sample for sample in wave if sample.event_id == "e-root")
    root.verifier_status = status
    root.local_reward = reward
    with pytest.raises(ReceiptInvariantError, match="invalid non-terminal verifier status"):
        validate_training_wave(wave)

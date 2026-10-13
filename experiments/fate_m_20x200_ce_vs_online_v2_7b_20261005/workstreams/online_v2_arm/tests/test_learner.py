from __future__ import annotations

import copy
from pathlib import Path
import random
import threading

import pytest
import torch

from alphaproof_online_v2_arm.learner import OnlineV2Config, OnlineV2Learner

from helpers import TinyPolicy, make_sample, make_validated_wave


def _learner(
    policy,
    behavior,
    anchor,
    ledger_path: Path,
    *,
    canary=lambda _: True,
    lr=0.005,
    value_head=None,
    micro_batch_size=1,
    update_epochs=2,
    scheduler=False,
    policy_version="policy-0001",
    wave_id="test-wave-0001",
):
    params = list(policy.parameters())
    if value_head is not None:
        params.extend(value_head.parameters())
    optimizer = torch.optim.AdamW(params, lr=lr)
    learning_rate_scheduler = (
        torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.9)
        if scheduler else None
    )
    return OnlineV2Learner(
        policy,
        behavior,
        anchor,
        optimizer,
        behavior_policy_version=policy_version,
        behavior_wave_id=wave_id,
        ledger_path=ledger_path,
        value_head=value_head,
        config=OnlineV2Config(
            update_epochs=update_epochs,
            micro_batch_size=micro_batch_size,
            min_independent_problems=2,
            target_behavior_kl=0.05,
            hard_behavior_kl_limit=0.5,
            hard_anchor_kl_limit=0.8,
            value_coef=1e-3 if value_head is not None else 0.0,
        ),
        canary=canary,
        scheduler=learning_rate_scheduler,
    )


def test_learner_refuses_loose_actor_samples(tmp_path: Path) -> None:
    behavior = TinyPolicy()
    learner = _learner(
        copy.deepcopy(behavior), behavior, copy.deepcopy(behavior), tmp_path / "ledger.json"
    )
    loose = [make_sample(behavior, problem_id="p1", trajectory_id="t1")]
    with pytest.raises(TypeError, match="ValidatedRolloutWave"):
        learner.update(loose)  # type: ignore[arg-type]


def test_learner_refuses_all_zero_policy_signal(tmp_path: Path) -> None:
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior, zero_policy_signal=True)
    learner = _learner(
        copy.deepcopy(behavior), behavior, copy.deepcopy(behavior), tmp_path / "ledger.json"
    )
    with pytest.raises(ValueError, match="no nonzero policy advantage"):
        learner.update(wave)


def test_old_logprob_mismatch_rolls_back_without_step(tmp_path: Path) -> None:
    torch.manual_seed(2)
    actor = TinyPolicy()
    wave = make_validated_wave(actor)
    wrong_behavior = copy.deepcopy(actor)
    with torch.no_grad():
        next(wrong_behavior.parameters()).add_(0.1)
    policy = copy.deepcopy(actor)
    learner = _learner(
        policy, wrong_behavior, copy.deepcopy(actor), tmp_path / "ledger.json"
    )
    before = copy.deepcopy(policy.state_dict())
    receipt = learner.update(wave)
    assert not receipt.accepted
    assert receipt.reason == "old_logprob_behavior_mismatch"
    assert receipt.optimizer_steps_this_update == 0
    assert all(torch.equal(before[k], policy.state_dict()[k]) for k in before)


def test_canary_rejection_restores_policy_and_optimizer(tmp_path: Path) -> None:
    torch.manual_seed(7)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    policy = copy.deepcopy(behavior)
    learner = _learner(
        policy,
        behavior,
        copy.deepcopy(behavior),
        tmp_path / "ledger.json",
        canary=lambda _: False,
    )
    before = copy.deepcopy(policy.state_dict())
    receipt = learner.update(wave)
    assert not receipt.accepted
    assert receipt.reason == "canary_rejected"
    assert receipt.optimizer_steps_total == 0
    assert receipt.optimizer_steps_this_update == 0
    assert all(torch.equal(before[k], policy.state_dict()[k]) for k in before)


def test_accepted_update_reports_streamed_kl_and_seals_wave(tmp_path: Path) -> None:
    torch.manual_seed(11)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    policy = copy.deepcopy(behavior)
    ledger = tmp_path / "ledger.json"
    learner = _learner(policy, behavior, copy.deepcopy(behavior), ledger)
    before = copy.deepcopy(policy.state_dict())
    receipt = learner.update(wave)
    assert receipt.accepted and learner.sealed
    assert receipt.micro_batches_per_epoch == len(wave)
    assert 1 <= receipt.epochs_completed <= 2
    assert receipt.behavior_exact_kl >= 0.0 and receipt.anchor_exact_kl >= 0.0
    assert any(not torch.equal(before[key], policy.state_dict()[key]) for key in before)
    with pytest.raises(RuntimeError, match="already committed"):
        learner.update(wave)
    restarted = _learner(
        copy.deepcopy(behavior), behavior, copy.deepcopy(behavior), ledger
    )
    assert restarted.sealed
    with pytest.raises(RuntimeError, match="already committed"):
        restarted.update(wave)


def test_commit_then_raise_recovers_committed_model_instead_of_rolling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the former ledger-commit/model-rollback split brain."""

    torch.manual_seed(23)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    policy = copy.deepcopy(behavior)
    ledger = tmp_path / "ledger.json"
    learner = _learner(policy, behavior, copy.deepcopy(behavior), ledger, update_epochs=1)
    before = copy.deepcopy(policy.state_dict())
    real_commit = learner.ledger.commit

    def commit_then_raise(*args, **kwargs):
        real_commit(*args, **kwargs)
        raise RuntimeError("injected exception after durable commit")

    monkeypatch.setattr(learner.ledger, "commit", commit_then_raise)
    receipt = learner.update(wave)
    assert receipt.accepted
    assert learner.sealed
    committed = copy.deepcopy(policy.state_dict())
    assert any(not torch.equal(before[key], committed[key]) for key in before)

    restarted_policy = copy.deepcopy(behavior)
    restarted = _learner(
        restarted_policy, behavior, copy.deepcopy(behavior), ledger, update_epochs=1
    )
    assert restarted.sealed
    assert all(
        torch.equal(committed[key], restarted_policy.state_dict()[key])
        for key in committed
    )


def test_two_learner_instances_competing_for_one_wave_only_commit_once(
    tmp_path: Path,
) -> None:
    torch.manual_seed(29)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    ledger = tmp_path / "ledger.json"
    policies = [copy.deepcopy(behavior), copy.deepcopy(behavior)]
    learners = [
        _learner(
            policies[index],
            copy.deepcopy(behavior),
            copy.deepcopy(behavior),
            ledger,
            update_epochs=1,
        )
        for index in range(2)
    ]
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []
    outcomes_lock = threading.Lock()

    def run(learner: OnlineV2Learner) -> None:
        barrier.wait()
        try:
            result: tuple[str, object] = ("receipt", learner.update(wave))
        except Exception as exc:  # the losing instance must observe COMMITTED
            result = ("error", exc)
        with outcomes_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=run, args=(learner,)) for learner in learners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()

    receipts = [item for kind, item in outcomes if kind == "receipt"]
    errors = [item for kind, item in outcomes if kind == "error"]
    assert len(receipts) == 1 and receipts[0].accepted
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "already committed" in str(errors[0])
    assert all(
        torch.equal(policies[0].state_dict()[key], policies[1].state_dict()[key])
        for key in policies[0].state_dict()
    )


def test_prepared_crash_is_restored_and_wave_can_be_retried(tmp_path: Path) -> None:
    torch.manual_seed(31)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    ledger = tmp_path / "ledger.json"
    original_policy = copy.deepcopy(behavior)
    crashed = _learner(
        original_policy, behavior, copy.deepcopy(behavior), ledger, update_epochs=1
    )
    original = copy.deepcopy(original_policy.state_dict())
    with crashed.ledger.locked():
        crashed.ledger.begin(
            crashed.behavior_wave_id,
            crashed.behavior_policy_version,
            wave.content_sha256,
            crashed._snapshot(),
        )

    # ``behavior`` was frozen as a reference by the first learner; create a
    # fresh trainable facade before injecting the simulated dirty state.
    dirty_policy = TinyPolicy()
    dirty_policy.load_state_dict(behavior.state_dict())
    with torch.no_grad():
        next(dirty_policy.parameters()).add_(7.0)
    recovered = _learner(
        dirty_policy, behavior, copy.deepcopy(behavior), ledger, update_epochs=1
    )
    assert not recovered.sealed
    assert all(
        torch.equal(original[key], dirty_policy.state_dict()[key]) for key in original
    )
    assert recovered.update(wave).accepted


def test_microbatch_matches_full_batch_update(tmp_path: Path) -> None:
    torch.manual_seed(13)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior)
    policy_micro = copy.deepcopy(behavior)
    policy_full = copy.deepcopy(behavior)
    micro = _learner(
        policy_micro,
        copy.deepcopy(behavior),
        copy.deepcopy(behavior),
        tmp_path / "micro.json",
        micro_batch_size=1,
        update_epochs=1,
        lr=0.001,
    )
    full = _learner(
        policy_full,
        copy.deepcopy(behavior),
        copy.deepcopy(behavior),
        tmp_path / "full.json",
        micro_batch_size=len(wave),
        update_epochs=1,
        lr=0.001,
    )
    micro.update(wave)
    full.update(wave)
    for key, value in policy_micro.state_dict().items():
        assert torch.allclose(value, policy_full.state_dict()[key], atol=2e-6, rtol=2e-6)


def test_new_update_requires_explicit_new_behavior_wave(tmp_path: Path) -> None:
    torch.manual_seed(17)
    behavior = TinyPolicy()
    first = make_validated_wave(behavior)
    policy = copy.deepcopy(behavior)
    learner = _learner(
        policy, behavior, copy.deepcopy(behavior), tmp_path / "ledger.json", update_epochs=1
    )
    assert learner.update(first).accepted
    new_behavior = copy.deepcopy(policy)
    second = make_validated_wave(
        new_behavior, wave_id="test-wave-0002", policy_version="policy-0002"
    )
    learner.install_behavior_wave(
        new_behavior, policy_version="policy-0002", wave_id="test-wave-0002"
    )
    assert not learner.sealed
    assert learner.update(second).accepted
    assert learner.sealed


def test_cross_wave_state_restores_optimizer_scheduler_rng_and_counters(
    tmp_path: Path,
) -> None:
    import numpy as np

    random.seed(41)
    np.random.seed(41)
    torch.manual_seed(41)
    behavior = TinyPolicy()
    policy = copy.deepcopy(behavior)
    wave1 = make_validated_wave(behavior)
    first = _learner(
        policy, behavior, copy.deepcopy(behavior), tmp_path / "wave1-ledger.json",
        update_epochs=1, scheduler=True,
    )
    first_receipt = first.update(wave1)
    assert first_receipt.accepted and first_receipt.optimizer_steps_total == 1
    state = first.export_training_state()
    expected_rng = (random.random(), float(torch.rand(())), float(np.random.random()))

    next_policy = copy.deepcopy(policy)
    next_behavior = copy.deepcopy(policy)
    wave2 = make_validated_wave(
        next_behavior, wave_id="test-wave-0002", policy_version="policy-0002"
    )
    second = _learner(
        next_policy, next_behavior, copy.deepcopy(behavior),
        tmp_path / "wave2-ledger.json", update_epochs=1, scheduler=True,
        policy_version="policy-0002", wave_id="test-wave-0002",
    )
    assert not second.optimizer.state
    assert second.optimizer_steps == 0
    second.restore_training_state(state)
    assert second.optimizer.state
    assert second.optimizer_steps == 1
    assert second.scheduler.state_dict() == first.scheduler.state_dict()
    observed_rng = (random.random(), float(torch.rand(())), float(np.random.random()))
    assert observed_rng == expected_rng

    second_receipt = second.update(wave2)
    assert second_receipt.accepted
    assert second_receipt.optimizer_steps_total == 2
    assert second_receipt.optimizer_steps_this_update == 1


def test_verified_path_value_head_uses_problem_balanced_ce64(tmp_path: Path) -> None:
    torch.manual_seed(19)
    behavior = TinyPolicy()
    wave = make_validated_wave(behavior, verified_paths=True)
    policy = copy.deepcopy(behavior)
    value_head = torch.nn.Linear(8, 64)
    learner = _learner(
        policy,
        behavior,
        copy.deepcopy(behavior),
        tmp_path / "ledger.json",
        value_head=value_head,
        update_epochs=1,
    )
    before = copy.deepcopy(value_head.state_dict())
    receipt = learner.update(wave)
    assert receipt.accepted
    assert receipt.value_samples == 2
    assert receipt.value_loss > 0
    assert any(not torch.equal(before[key], value_head.state_dict()[key]) for key in before)


def test_update_epoch_cap_is_structural() -> None:
    with pytest.raises(ValueError, match="update_epochs"):
        OnlineV2Config(update_epochs=3).validate()


def test_positive_unverified_reward_is_rejected_before_training() -> None:
    model = TinyPolicy()
    sample = make_sample(model, problem_id="p", trajectory_id="t", local_reward=1.0)
    with pytest.raises(ValueError, match="positive reward"):
        sample.validate()

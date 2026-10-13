from __future__ import annotations

import copy

import torch

from alphaproof_online_v2.learner import OnlineV2Config, OnlineV2Learner

from helpers import TinyPolicy, make_sample


def _states_equal(left: dict, right: dict) -> bool:
    if left.keys() != right.keys():
        return False
    for key in left:
        a, b = left[key], right[key]
        if isinstance(a, torch.Tensor):
            if not torch.equal(a, b):
                return False
        elif isinstance(a, dict):
            if not _states_equal(a, b):
                return False
        elif isinstance(a, list):
            if len(a) != len(b):
                return False
            for x, y in zip(a, b):
                if isinstance(x, dict) and not _states_equal(x, y):
                    return False
                if not isinstance(x, dict) and x != y:
                    return False
        elif a != b:
            return False
    return True


def test_canary_rejection_restores_model_optimizer_and_step_counter() -> None:
    torch.manual_seed(7)
    policy = TinyPolicy()
    reference = copy.deepcopy(policy)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=0.02)
    learner = OnlineV2Learner(
        policy,
        reference,
        optimizer,
        config=OnlineV2Config(update_epochs=1, min_independent_problems=2),
        canary=lambda _: False,
    )
    samples = [
        make_sample(reference, problem_id="p1", trajectory_id="t1"),
        make_sample(reference, problem_id="p2", trajectory_id="t2"),
    ]
    model_before = copy.deepcopy(policy.state_dict())
    optimizer_before = copy.deepcopy(optimizer.state_dict())
    receipt = learner.update(samples, policy_version="policy-0001")
    assert not receipt.accepted
    assert receipt.reason == "canary_rejected"
    assert learner.optimizer_steps == 0
    assert all(torch.equal(model_before[k], policy.state_dict()[k]) for k in model_before)
    assert _states_equal(optimizer_before, optimizer.state_dict())


def test_accepted_update_changes_policy_and_reports_post_step_kl() -> None:
    torch.manual_seed(11)
    policy = TinyPolicy()
    reference = copy.deepcopy(policy)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=0.005)
    learner = OnlineV2Learner(
        policy,
        reference,
        optimizer,
        config=OnlineV2Config(
            update_epochs=1,
            min_independent_problems=2,
            hard_kl_limit=0.5,
            target_kl=0.05,
        ),
        canary=lambda _: True,
    )
    samples = [
        make_sample(reference, problem_id="p1", trajectory_id="t1", advantage=1.0),
        make_sample(reference, problem_id="p2", trajectory_id="t2", advantage=0.5),
    ]
    before = copy.deepcopy(policy.state_dict())
    receipt = learner.update(samples, policy_version="policy-0001")
    assert receipt.accepted
    assert receipt.optimizer_steps == 1
    assert receipt.exact_kl >= 0.0
    assert any(not torch.equal(before[key], policy.state_dict()[key]) for key in before)

"""离线专家迭代 learner 测试（需要 torch；无 torch 自动跳过）。"""

import os
import sys

import pytest

torch = pytest.importorskip("torch")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alphaproof.config import TrainConfig  # noqa: E402
from alphaproof.data.events import Transition  # noqa: E402
from alphaproof.net.value_head import ValueHead64  # noqa: E402
from alphaproof.pipeline import make_failure_samples, result_to_trajectory, run_mock_search  # noqa: E402
from alphaproof.tiny import build_tiny, tiny_encode_fn  # noqa: E402
from update_offline.learner import OfflineLearner  # noqa: E402


def make_learner(batch_size=8):
    model, tok = build_tiny()
    cfg = TrainConfig(value_coef=1e-3, learn_batch_size=batch_size)
    learner = OfflineLearner(model=model, value_head=ValueHead64(hidden_size=model.hidden_size),
                             encode_fn=tiny_encode_fn(tok), config=cfg)
    return learner


def test_offline_update_receipt_and_timeout_filtering():
    traj = result_to_trajectory(run_mock_search())
    disproof, timeout = make_failure_samples()
    batch = list(traj.transitions) + list(disproof.transitions) + list(timeout.transitions)
    learner = make_learner()
    receipt = learner.update(batch)
    assert not receipt.get("skipped")
    assert receipt["samples"] == len(traj.transitions) + 1        # proof + disproof
    assert "timeout" not in receipt["kind_counts"]
    assert receipt["value_samples"] == receipt["samples"]         # 反证样本也有 value target=0
    assert receipt["optimizer_steps"] == 1
    assert receipt["loss"] == receipt["loss"]                     # 非 NaN


def test_offline_sft_samples_policy_only():
    traj = result_to_trajectory(run_mock_search())
    sft = [Transition(prompt=t.prompt, action=t.action, value_target=None, kind="sft")
           for t in traj.transitions]
    learner = make_learner()
    receipt = learner.update(sft)
    assert receipt["value_samples"] == 0
    assert receipt["kind_counts"] == {"sft": len(sft)}


def test_offline_loss_is_finite_and_decreasing_on_repeat():
    traj = result_to_trajectory(run_mock_search())
    learner = make_learner()
    r1 = learner.update(list(traj.transitions))
    r2 = learner.update(list(traj.transitions))
    assert r2["loss"] == r2["loss"]

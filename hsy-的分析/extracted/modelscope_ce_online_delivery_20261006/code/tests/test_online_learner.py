"""在线更新 learner 测试（需要 torch；无 torch 自动跳过）：batch 语义与安全校验。"""

import os
import sys

import pytest

torch = pytest.importorskip("torch")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alphaproof.config import TrainConfig  # noqa: E402
from alphaproof.data.events import Transition  # noqa: E402
from alphaproof.net.value_head import ValueHead64  # noqa: E402
from alphaproof.pipeline import result_to_trajectory, run_mock_search  # noqa: E402
from alphaproof.tiny import build_tiny, tiny_encode_fn  # noqa: E402
from update_online.learner import OnlineLearner  # noqa: E402


def make_events(count=3, reward=1.0, verified=True):
    traj = result_to_trajectory(run_mock_search())
    events = []
    for t in traj.transitions[:count]:
        events.append(Transition(prompt=t.prompt, action=t.action, value_target=t.value_target,
                                 kind="proof", solved=True, terminal_verified=verified,
                                 reward=reward, logprob_old=-1.0))
    return events


def make_learner(batch_size):
    model, tok = build_tiny()
    cfg = TrainConfig(learn_batch_size=batch_size, value_coef=1e-3)
    learner = OnlineLearner(model=model, value_head=ValueHead64(hidden_size=model.hidden_size),
                            encode_fn=tiny_encode_fn(tok), config=cfg)
    return learner


def test_batch_size_1_updates_per_event():
    learner = make_learner(batch_size=1)
    receipts = [learner.submit(e) for e in make_events(3)]
    assert all(r is not None for r in receipts)
    assert [r["samples"] for r in receipts] == [1, 1, 1]
    assert learner.update_index == 3


def test_batch_size_n_accumulates_then_updates():
    learner = make_learner(batch_size=2)
    receipts = [learner.submit(e) for e in make_events(3)]
    assert receipts[0] is None and receipts[1] is not None and receipts[2] is None
    assert receipts[1]["samples"] == 2
    tail = learner.flush()
    assert tail is not None and tail["samples"] == 1
    assert learner.update_index == 2


def test_positive_reward_requires_verification():
    learner = make_learner(batch_size=1)
    bad = make_events(1, reward=1.0, verified=False)[0]
    with pytest.raises(ValueError):
        learner.submit(bad)


def test_value_target_optional_but_no_r_fallback():
    learner = make_learner(batch_size=1)
    e = make_events(1)[0]
    e.value_target = None          # 不再回退用 r 当 value target
    receipt = learner.submit(e)
    assert receipt is not None and receipt["value_samples"] == 0

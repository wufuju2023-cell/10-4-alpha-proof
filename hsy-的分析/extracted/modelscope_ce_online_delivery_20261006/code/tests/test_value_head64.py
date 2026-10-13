"""64 桶价值头测试（需要 torch；无 torch 自动跳过）。"""

import os
import sys

import pytest

torch = pytest.importorskip("torch")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alphaproof.net.value_head import (ValueHead64, expected_distance,  # noqa: E402
                                       load_s18_head, two_hot_target)


def test_value_head64_shapes_and_decode():
    head = ValueHead64(hidden_size=32, mid=8)
    logits = head(torch.randn(4, 32))
    assert logits.shape == (4, 64)
    d = head.decode(logits)
    assert d.shape == (4,)
    assert ((d >= 1) & (d <= 64)).all()


def test_two_hot_and_expected_distance_consistency():
    head = ValueHead64(hidden_size=8, mid=4)
    # 构造接近 one-hot 的 logits，验证 decode 接近目标
    logits = torch.full((1, 64), -10.0)
    logits[0, 20] = 10.0
    d = expected_distance(logits)
    assert abs(float(d) - 21.0) < 1e-3
    t = two_hot_target(torch.tensor([21.0]))
    assert abs(float(t.sum()) - 1.0) < 1e-6
    assert t[0, 20] == 1.0


def test_load_s18_head_roundtrip(tmp_path):
    head = ValueHead64(hidden_size=16, mid=4)
    path = tmp_path / "head.pt"
    torch.save(head.state_dict(), path)
    head2 = ValueHead64(hidden_size=16, mid=4)
    report = load_s18_head(head2, str(path))
    assert not report["missing"] and not report["unexpected"]


def test_load_s18_head_prefixed_and_wrapped(tmp_path):
    """云端实际格式：键名无 'net.' 前缀（0.weight...），可能包在 {'value_head': ...}。"""
    head = ValueHead64(hidden_size=16, mid=4)
    legacy = {k.replace("net.", ""): v for k, v in head.state_dict().items()}
    p1 = tmp_path / "legacy.pt"
    torch.save(legacy, p1)
    head2 = ValueHead64(hidden_size=16, mid=4)
    r1 = load_s18_head(head2, str(p1))
    assert not r1["missing"] and not r1["unexpected"]

    p2 = tmp_path / "wrapped.pt"
    torch.save({"value_head": legacy}, p2)
    head3 = ValueHead64(hidden_size=16, mid=4)
    r2 = load_s18_head(head3, str(p2))
    assert not r2["missing"] and not r2["unexpected"]

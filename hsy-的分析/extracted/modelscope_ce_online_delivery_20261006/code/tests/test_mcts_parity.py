"""MCTS 对齐测试（纯 Python）：PUCT 常量语义 / G2 / G3 / 完整 Pell Mock 搜索。"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alphaproof.config import SearchConfig  # noqa: E402
from alphaproof.env.pell_mock import (EXPECTED_PELL_SCRIPT, PellMockEnv,  # noqa: E402
                                      mock_policy, mock_value)
from alphaproof.mcts.search import MCTS  # noqa: E402
from alphaproof.mcts.tree import AND, OR, Node  # noqa: E402
from alphaproof.targets.value_targets import extract_transitions  # noqa: E402


def make_simple_mcts():
    cfg = SearchConfig()
    return MCTS(env=PellMockEnv(), policy=mock_policy, value=mock_value, config=cfg)


def test_prior_normalization_g3():
    """G3：先验按 totalMass 归一（先验 30:10 → U 比 3:1）。"""
    mcts = make_simple_mcts()
    parent = Node(state_key="p", state_repr="P", to_play=OR)
    parent.num_visit = 1
    parent.value_sum = -1.0
    for name, prior in (("a", 30.0), ("b", 10.0)):
        c = Node(state_key=name, state_repr=name)
        c.num_visit = 1
        c.value_sum = -1.0
        parent.add_child(name, c, prior=prior)
    scores = mcts.puct_scores(parent)
    q = mcts.config.visit_discount ** (-1.0 - (-1.0 - 1.0))       # 访问过：value=-1，边代价 1
    u_a = scores["a"] - q
    u_b = scores["b"] - q
    assert math.isclose(u_a / u_b, 3.0, rel_tol=1e-9)


def test_c_and_multiplier_and_unvisited_penalty():
    cfg = SearchConfig()
    mcts = make_simple_mcts()
    # visited child：value=-3, step_cost=1 → Q=γ^(-1-(-4))
    or_parent = Node(state_key="o", state_repr="O", to_play=OR)
    or_parent.num_visit = 4
    or_parent.value_sum = -4.0
    child = Node(state_key="c", state_repr="C")
    child.num_visit = 2
    child.value_sum = -6.0  # value=-3
    or_parent.add_child("t", child, prior=1.0)
    or_parent.edges["t"].num_visit = 2
    q_or = cfg.visit_discount ** (-1.0 - (-3.0 - 1.0))
    c = cfg.c_init + math.log((4 + cfg.c_base + 1) / cfg.c_base)
    u_shared = c * 1.0 * math.sqrt(4) / (2 + 1)
    assert math.isclose(mcts.puct_scores(or_parent)["t"], q_or + u_shared, rel_tol=1e-12)

    # AND：Q 变为 1-Q（focus 边代价 0），U 乘 c_AND=64
    and_parent = Node(state_key="a", state_repr="A", to_play=AND)
    and_parent.num_visit = 4
    and_parent.value_sum = -4.0
    fchild = Node(state_key="fc", state_repr="FC")
    fchild.num_visit = 2
    fchild.value_sum = -6.0
    and_parent.add_child("focus_goal 0", fchild, prior=1.0, is_focus=True)
    and_parent.edges["focus_goal 0"].num_visit = 2
    q_focus = cfg.visit_discount ** (-1.0 - (-3.0 - 0.0))
    score_and = mcts.puct_scores(and_parent)["focus_goal 0"]
    assert math.isclose(score_and, (1.0 - q_focus) + cfg.c_and * u_shared, rel_tol=1e-9)

    # 未访问子：V(s,a) = V(s) - c_pen
    parent2 = Node(state_key="p2", state_repr="P2", to_play=OR)
    parent2.num_visit = 2
    parent2.value_sum = -8.0  # V(s)=-4
    fresh = Node(state_key="f", state_repr="F")
    parent2.add_child("n", fresh, prior=1.0)
    q = cfg.visit_discount ** (-1.0 - (-4.0 - cfg.unvisited_penalty))
    c2 = cfg.c_init + math.log((2 + cfg.c_base + 1) / cfg.c_base)
    u2 = c2 * 1.0 * math.sqrt(2) / (0 + 1)
    assert math.isclose(mcts.puct_scores(parent2)["n"], q + u2, rel_tol=1e-12)


def test_progressive_sampling_condition():
    mcts = make_simple_mcts()
    node = Node(state_key="n", state_repr="N", to_play=OR)
    node.num_evaluations = 1
    node.num_visit = 1
    assert not mcts._progressive_sample(node)          # 1 <= 0.01*1^0.6 → False
    node.num_visit = 100_000                            # 0.01*100000^0.6 = 10
    assert mcts._progressive_sample(node)               # 1 <= 10 → True
    node.to_play = AND
    assert not mcts._progressive_sample(node)           # 仅 OR


def test_g2_reexpansion_never_resets_children():
    """G2：渐进采样重新扩展时 children 只增不减，先验累加。"""
    mcts = make_simple_mcts()
    node = Node(state_key="pell.root", state_repr="|- CodexMathFive.Pell.Target", to_play=OR)
    node.num_visit = 1
    mcts._expand(node)
    first = dict(node.children)
    assert len(first) >= 1
    priors_before = {k: node.edges[k].prior for k in first}
    mcts._expand(node)  # 再次扩展（模拟渐进采样）
    assert set(node.children) >= set(first)             # 不重置
    for k, v in priors_before.items():
        assert node.edges[k].prior >= v                 # 先验只增


def test_full_pell_mock_search_and_value_targets():
    mcts = make_simple_mcts()
    result = mcts.search("pell.root")
    assert result.solved
    assert result.best_script == EXPECTED_PELL_SCRIPT
    assert result.nodes_created >= 6
    transitions = extract_transitions(result.root)
    actions = [a for _, a, _ in transitions]
    assert actions == ["intro B", "refine <17, 12, ?_, ?_>", "norm_num", "norm_num"]
    # root(-3) -> B(-2) -> [eq(-1), bounds(-1)]
    assert [v for _, _, v in transitions] == [-3.0, -2.0, -1.0, -1.0]

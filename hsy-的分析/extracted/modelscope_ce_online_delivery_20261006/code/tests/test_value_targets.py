"""价值目标对齐测试（纯 Python，无需 torch）：官方 compute_value_target + 两热编码。"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alphaproof.mcts.tree import AND, OR, Node  # noqa: E402
from alphaproof.targets.value_targets import (compute_value_target,  # noqa: E402
                                              distance_to_two_hot, extract_transitions)


def build_tree():
    """N-27 的例子：root -> AND(split) -> [x->leaf, y->leaf]，距离分别为 2 和 2。"""
    leaf_a = Node(state_key="a", state_repr="A", terminal=True)
    leaf_b = Node(state_key="b", state_repr="B", terminal=True)
    node_a = Node(state_key="na", state_repr="NA", to_play=OR)
    node_a.add_child("to-a", leaf_a, prior=1.0)
    node_b = Node(state_key="nb", state_repr="NB", to_play=OR)
    node_b.add_child("to-b", leaf_b, prior=1.0)
    and_node = Node(state_key="and", state_repr="AND", to_play=AND)
    and_node.add_child("a", node_a, prior=0.5, is_focus=True)
    and_node.add_child("b", node_b, prior=0.5, is_focus=True)
    root = Node(state_key="root", state_repr="ROOT", to_play=OR)
    root.add_child("split", and_node, prior=1.0)
    for n in (leaf_a, leaf_b, node_a, node_b, and_node, root):
        n.refresh_solved()
    # 标记最优路径
    def mark(node):
        node.is_optimal = True
        for c in node.children.values():
            if c.solved:
                mark(c)
    mark(root)
    return root, node_a, node_b


def test_compute_value_target_minimax():
    root, node_a, node_b = build_tree()
    value = compute_value_target(root)
    # root: -1 + AND; AND: min(-1+0, -1+0) = -1; root = -2
    assert value == -2.0
    assert root.value_target == -2.0
    assert node_a.value_target == -1.0 and node_b.value_target == -1.0


def test_extract_transitions_only_optimal_path():
    root, _, _ = build_tree()
    transitions = extract_transitions(root)
    actions = [a for _, a, _ in transitions]
    assert actions == ["split", "to-a", "to-b"]
    values = [v for _, _, v in transitions]
    assert values == [-2.0, -1.0, -1.0]
    # focus 伪动作不应作为样本出现
    assert all(not a.startswith("focus_goal") for a in actions)


def test_two_hot_sums_to_one_and_clamps():
    t = distance_to_two_hot(7.5)
    assert math.isclose(sum(t), 1.0)
    assert math.isclose(t[6], 0.5) and math.isclose(t[7], 0.5)  # 桶 7 与 8
    t0 = distance_to_two_hot(0.0)     # 终端 d=0 → 截断到桶 1
    assert t0[0] == 1.0
    t_hi = distance_to_two_hot(999.0)  # 超界 → 桶 64
    assert t_hi[-1] == 1.0
    t_int = distance_to_two_hot(5.0)
    assert t_int[4] == 1.0

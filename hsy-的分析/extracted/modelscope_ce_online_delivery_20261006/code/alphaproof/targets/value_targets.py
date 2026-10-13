"""官方价值目标的直译（修 G1）。

pseudocode.py::compute_value_target：
- 终端（证明/反证完成）：0；
- OR 节点：-1 + value(最优路径子节点)；
- AND 节点：min(子节点)（最坏分支决定）。

注意：value_target = -d(s)，d 为剩余步数；训练时用 d 分桶（1..64）。
历史实现把标量 r 当 value target 的兜底在 `update_online` 中已移除。
"""

from __future__ import annotations

import math
from typing import List, Tuple

from ..mcts.tree import AND, OR, Node


def select_optimal_child(node: Node) -> Tuple[str, Node]:
    """官方 select_optimal_action：取 is_optimal 的子节点（OR 用）。"""
    for action, child in node.children.items():
        if child.is_optimal:
            return action, child
    raise ValueError(f"node {node.state_key!r} has no optimal child (is_optimal not set)")


def compute_value_target(node: Node) -> float:
    """递归计算并写回 node.value_target（官方语义）。"""
    if node.terminal:
        node.value_target = 0.0
        return 0.0
    if node.to_play == OR:
        _, child = select_optimal_child(node)
        value = -1.0 + compute_value_target(child)
    else:
        if not node.children:
            raise ValueError(f"AND node {node.state_key!r} has no children")
        value = min(compute_value_target(c) for c in node.children.values())
    node.value_target = value
    return value


def extract_transitions(root: Node) -> List[Tuple[str, str, float]]:
    """官方 extract_transitions：沿最优路径收集 (state, action, value_target)。

    - 仅对 is_optimal 子树生效；
    - OR 链上收集 (state, action, value_target)；
    - 遇到 AND 子节点时对每个 focus 分支递归（focus 伪动作不进样本）。
    """
    compute_value_target(root)
    out: List[Tuple[str, str, float]] = []

    def walk(node: Node) -> None:
        if node.terminal or not node.is_optimal:
            return
        if node.to_play == OR:
            for action, child in node.children.items():
                if not child.is_optimal:
                    continue
                if not action.startswith("focus_goal"):
                    out.append((node.state_repr, action, float(node.value_target or 0.0)))
                walk(child)
                break
        else:
            for child in node.children.values():
                walk(child)

    walk(root)
    return out


def distance_to_two_hot(distance: float, bins: int = 64) -> List[float]:
    """剩余步数 d → 两个相邻桶的 one/two-hot 分布（官方/云端值头两热编码）。

    d 先截断到 [1, bins]（终端 d=0 落到桶 1，与云端 `clamp(d,1,64)` 一致），
    再按小数部分线性分配到 floor/ceil 两桶。
    """
    if bins <= 1:
        raise ValueError("bins must be >= 2")
    d = min(max(float(distance), 1.0), float(bins))
    lower = int(math.floor(d))
    upper = min(lower + 1, bins)
    frac = d - lower
    target = [0.0] * bins
    target[lower - 1] += 1.0 - frac
    if upper != lower:
        target[upper - 1] += frac
    else:
        target[lower - 1] = 1.0
    return target

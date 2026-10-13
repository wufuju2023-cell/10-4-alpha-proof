"""搜索树数据结构。

对齐 Lean `TreeSearch.lean` 的 NodeData / EdgeData：
- 节点：stateKey（去重键）、toPlay（OR/AND）、isSolved、valueSum、numVisit、numEvaluations；
- 边：tacticStr、probability（先验，展开时不做归一——归一在打分时按 totalMass 做，修 G3）、
  isFocus（focus 伪动作）、numVisit、value。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

OR = "OR"
AND = "AND"


@dataclass
class Edge:
    action: str
    prior: float = 0.0
    is_focus: bool = False
    num_visit: int = 0
    value: float = 0.0

    @property
    def step_cost(self) -> float:
        """focus 边代价 0，普通战术边代价 1（官方 reward = -1 的细化）。"""
        return 0.0 if self.is_focus else 1.0


@dataclass
class Node:
    state_key: str
    state_repr: str
    to_play: str = OR
    terminal: bool = False
    solved: bool = False
    is_optimal: bool = False
    net_value: float = 0.0          # V̂ = -d̂（网络估计，展开时写入）
    value_target: Optional[float] = None  # -d(s)，compute_value_target 写入
    value_sum: float = 0.0
    num_visit: int = 0
    num_evaluations: int = 0
    parent: Optional["Node"] = None
    parent_action: Optional[str] = None
    children: Dict[str, "Node"] = field(default_factory=dict)
    edges: Dict[str, Edge] = field(default_factory=dict)

    # --- 统计 ---
    def value(self) -> float:
        return self.value_sum / self.num_visit if self.num_visit else 0.0

    def expanded(self) -> bool:
        return bool(self.children)

    # --- 结构操作 ---
    def add_child(self, action: str, child: "Node", prior: float, is_focus: bool = False) -> None:
        self.children[action] = child
        self.edges[action] = Edge(action=action, prior=prior, is_focus=is_focus)
        child.parent = self
        child.parent_action = action

    def refresh_solved(self) -> None:
        """OR：任一子解出；AND：全部子解出；terminal 自身即解出。"""
        if self.terminal:
            self.solved = True
            return
        if not self.children:
            return
        if self.to_play == OR:
            self.solved = any(c.solved for c in self.children.values())
        else:
            self.solved = all(c.solved for c in self.children.values())

    def refresh_solved_up(self) -> None:
        node: Optional[Node] = self
        while node is not None:
            node.refresh_solved()
            node = node.parent


def backprop_towards_min(node: Node) -> float:
    """AND 回传：未解出且已访问子的最小 value；全无则中性值 1.0。"""
    values = [c.value() for c in node.children.values() if not c.solved and c.num_visit > 0]
    return min(values) if values else 1.0

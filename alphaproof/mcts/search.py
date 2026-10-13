"""对齐版 PUCT MCTS（纯 Python，可单测）。

相对历史的修正（N33：G1 在 targets、G2/G3 在本文件）：
- G2：渐进采样只增不减 —— 重新扩展时**不重置** children；
- G3：先验在打分时按 totalMass 归一（等价 Lean `computePUCTScores` / 官方 `prior_sum()`）；
- D6：AND 节点探索项乘 c_AND=64；未访问子节点 Q 用 V(s)-c_pen（c_pen=32）；
- D2：无合法动作/失败兜底 -40 由上层（env/网络侧）使用，本文件提供配置；
- D1：τ 默认 200，运行时可改；
- E5：`SearchConfig.optimal_child_policy` 选择规则旋钮（first/most_visited/shortest_action）；
  每次尝试记录在 `self.attempts`，供 `data/tree_export.py` 导出完整树与失败信息。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from ..config import SearchConfig
from ..env.base import LeanEnv
from .tree import AND, OR, Node, backprop_towards_min

PolicyFn = Callable[[str], List[Tuple[str, float]]]   # state_repr -> [(tactic, logprob)]
ValueFn = Callable[[str], float]                      # state_repr -> V̂ = -d̂


@dataclass
class SearchResult:
    root: Node
    solved: bool
    nodes_created: int
    simulations: int
    best_script: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "solved": self.solved,
            "nodes_created": self.nodes_created,
            "simulations": self.simulations,
            "best_script": self.best_script,
        }


class MCTS:
    def __init__(
        self,
        env: LeanEnv,
        policy: PolicyFn,
        value: Optional[ValueFn] = None,
        config: Optional[SearchConfig] = None,
    ) -> None:
        self.env = env
        self.policy = policy
        self.value = value
        self.config = config or SearchConfig()
        self._nodes_created = 0
        # 每次尝试（node, tactic）的记录，供 tree_export 导出（E5 前置件）
        self.attempts: list[dict] = []

    # ------------------------------------------------------------------ #
    # 对外主循环（官方 run_mcts：选择 → 扩展 → 回传，直到证明或预算耗尽）
    # ------------------------------------------------------------------ #
    def search(self, root_state: str, root_repr: Optional[str] = None) -> SearchResult:
        cfg = self.config
        self._nodes_created = 1
        root = Node(
            state_key=root_state,
            state_repr=root_repr if root_repr is not None else self.env.pp_state(root_state),
        )
        sims = 0
        for sim in range(cfg.max_steps):
            if root.solved:
                break
            if self._nodes_created >= cfg.max_nodes:
                break
            sims = sim + 1

            path_nodes: List[Node] = [root]
            path_edges = []
            node = root
            # 选择：expanded 且未触发渐进采样时向下走
            while node.expanded() and not node.terminal and not self._progressive_sample(node):
                child = self._select(node)
                if child is None:
                    break
                path_edges.append(node.edges[child.parent_action])  # type: ignore[arg-type]
                path_nodes.append(child)
                node = child

            # 扩展（含渐进采样的“重新扩展”，只增不减）
            if node.terminal:
                leaf_value = 0.0
            else:
                leaf_value = self._expand(node)

            self._backprop(path_nodes, path_edges, leaf_value)
            for n in path_nodes:
                n.refresh_solved()

        root.refresh_solved_up()
        if root.solved:
            self._mark_optimal(root, cfg.optimal_child_policy)
        script = self._collect_script(root) if root.solved else []
        return SearchResult(root=root, solved=root.solved,
                            nodes_created=self._nodes_created, simulations=sims,
                            best_script=script)

    # ------------------------------------------------------------------ #
    # 评分（官方 ucb_score + Table 3 的 c_AND / 未访问惩罚）
    # ------------------------------------------------------------------ #
    def puct_scores(self, node: Node) -> dict[str, float]:
        cfg = self.config
        n = float(node.num_visit)
        c = cfg.c_init + math.log((n + cfg.c_base + 1) / cfg.c_base)
        total_mass = sum(e.prior for e in node.edges.values()) or 1.0
        parent_value = node.value()  # 父节点聚合价值 V(s)，未访问边的基准
        scores: dict[str, float] = {}
        for action, child in node.children.items():
            edge = node.edges[action]
            p = edge.prior / total_mass
            if edge.num_visit > 0 or child.num_visit > 0:
                value = child.value() - edge.step_cost
                q = cfg.visit_discount ** (-1.0 - value)
            else:
                # 官方：未访问 V(s,a) = V_net(s) - c_pen（Table 3: 32）
                q = cfg.visit_discount ** (-1.0 - (parent_value - cfg.unvisited_penalty))
            if node.to_play == AND:
                q = float("-inf") if child.solved else 1.0 - q
            u = c * p * math.sqrt(n) / (edge.num_visit + 1)
            if node.to_play == AND:
                u *= cfg.c_and
            scores[action] = q + u
        return scores

    def _select(self, node: Node) -> Optional[Node]:
        scores = self.puct_scores(node)
        if not scores:
            return None
        best_action = max(scores.items(), key=lambda kv: kv[1])[0]
        return node.children[best_action]

    def _progressive_sample(self, node: Node) -> bool:
        cfg = self.config
        return (
            node.to_play == OR
            and (node.num_evaluations <= cfg.ps_c * (node.num_visit ** cfg.ps_alpha))
        )

    # ------------------------------------------------------------------ #
    # 扩展（官方 expand_node；G2：不重置 children）
    # ------------------------------------------------------------------ #
    def _expand(self, node: Node) -> float:
        cfg = self.config
        if node.to_play == AND and node.children:
            return node.value()  # AND 节点不采样策略，仅调度 focus 子节点

        tactics = self.policy(node.state_repr) or []
        node.num_evaluations += 1

        # 网络价值 V̂ = -d̂；先计入自身统计（对齐 Lean visitNode）
        net = float(self.value(node.state_repr)) if self.value is not None else 0.0
        node.net_value = net
        node.value_sum += net
        node.num_visit += 1

        for action, logprob in tactics:
            if not isinstance(action, str) or not action.strip():
                continue
            prior = math.exp(float(logprob) / cfg.prior_temperature)
            attempt = {"node": node, "action": action, "prior": prior, "seq": len(self.attempts)}

            # 去重 1：同战术 → 先验累加（官方 expand_node）
            if action in node.children:
                node.edges[action].prior += prior
                attempt.update(kind="dedup_action", ok=True)
                self.attempts.append(attempt)
                continue

            result = self.env.apply(node.state_key, action)
            if not result.ok:
                attempt.update(kind="env_rejected", ok=False, error=str(result.error))
                self.attempts.append(attempt)
                continue

            # 去重 2：同状态 key → 合并（Lean: e.tacticStr == t || c.key == childData.key）
            dup = next((k for k, c in node.children.items() if c.state_key == result.next_state), None)
            if dup is not None:
                node.edges[dup].prior += prior
                attempt.update(kind="dedup_state", ok=True, child_state_key=result.next_state)
                self.attempts.append(attempt)
                continue

            closed = bool(result.closed) or result.num_goals == 0
            child = Node(
                state_key=result.next_state or f"{node.state_key}::{action}",
                state_repr=self.env.pp_state(result.next_state) if result.next_state else "",
                to_play=AND if result.num_goals > 1 else OR,
                terminal=closed,
            )
            node.add_child(action, child, prior)
            self._nodes_created += 1
            attempt.update(kind="accepted", ok=True, child_state_key=child.state_key)
            self.attempts.append(attempt)

            # AND 节点：立刻挂 focus 伪子节点（先验 1/k，边代价 0）
            if child.to_play == AND:
                subs = list(result.subgoal_states) or [f"subgoal-{i}" for i in range(result.num_goals)]
                for i, sub in enumerate(subs):
                    focus = Node(
                        state_key=sub,                       # 真实子目标句柄（供后续 env.apply）
                        state_repr=self.env.pp_state(sub) if result.subgoal_states else sub,
                        to_play=OR,
                        terminal=False,
                    )
                    child.add_child(f"focus_goal {i}", focus, 1.0 / len(subs), is_focus=True)
                    self._nodes_created += 1
                child.refresh_solved()

        node.refresh_solved()
        return net

    # ------------------------------------------------------------------ #
    # 回传（官方 backpropagate / backprop_value_towards_min）
    # ------------------------------------------------------------------ #
    def _backprop(self, path_nodes: List[Node], path_edges: list, leaf_value: float) -> None:
        value = leaf_value
        for i in range(len(path_nodes) - 1, 0, -1):
            parent = path_nodes[i - 1]
            edge = path_edges[i - 1]
            value = value - edge.step_cost
            edge.num_visit += 1
            edge.value += value
            parent.value_sum += value
            parent.num_visit += 1
            parent.refresh_solved()
            if parent.to_play == AND:
                value = backprop_towards_min(parent)

    # ------------------------------------------------------------------ #
    # 解路径 / 脚本
    # ------------------------------------------------------------------ #
    # 选择规则（E5 试验旋钮；默认 "first" = 历史行为/官方对齐）：
    # - "first"          ：按子节点插入顺序取第一个已解出子节点（原行为）
    # - "most_visited"   ：取访问次数最多的已解出子节点（MCTS 自然口径）
    # - "shortest_action"：取战术文本最短的已解出子节点（tie：访问多者优先）
    @staticmethod
    def _pick_optimal_child(node: Node, policy: str) -> Optional[str]:
        candidates = [(a, c) for a, c in node.children.items() if c.solved]
        if not candidates:
            return None
        if policy == "most_visited":
            return max(candidates, key=lambda ac: ac[1].num_visit)[0]
        if policy == "shortest_action":
            return min(candidates, key=lambda ac: (len(ac[0].split()), -ac[1].num_visit))[0]
        return candidates[0][0]

    @staticmethod
    def _mark_optimal(node: Node, policy: str = "first") -> None:
        node.is_optimal = True
        if node.terminal:
            return
        if node.to_play == OR:
            action = MCTS._pick_optimal_child(node, policy)
            if action is None:
                return
            child = node.children[action]
            child.is_optimal = True
            MCTS._mark_optimal(child, policy)
        else:
            for child in node.children.values():
                if child.solved:
                    child.is_optimal = True
                    MCTS._mark_optimal(child, policy)

    @staticmethod
    def _collect_script(node: Node) -> List[str]:
        if node.terminal:
            return []
        script: List[str] = []
        if node.to_play == OR:
            for action, child in node.children.items():
                if child.is_optimal:
                    if not action.startswith("focus_goal"):
                        script.append(action)
                    script.extend(MCTS._collect_script(child))
                    break
        else:
            for child in node.children.values():
                if child.is_optimal:
                    script.extend(MCTS._collect_script(child))
        return script

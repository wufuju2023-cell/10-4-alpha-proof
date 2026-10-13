"""E5/G2 前置：最优子节点选择规则（first / most_visited / shortest_action）。

对应 `reports/htps-selection-recheck.md` 的结论：FATE 波次的 first-verified
在离线重排里低于随机；本测试固定三种选择规则在本机 MCTS 中的行为，
防止后续改 runner 时回归。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaproof.config import SearchConfig  # noqa: E402
from alphaproof.env.pell_mock import PellMockEnv, mock_policy, mock_value  # noqa: E402
from alphaproof.mcts.search import MCTS  # noqa: E402
from alphaproof.mcts.tree import Node  # noqa: E402


def make_root_with_three_solved():
    """插入顺序 a→b→c；访问数 b 最高；动作长度 c 最短（1 token）。"""
    root = Node(state_key="root", state_repr="root")
    a = Node(state_key="sa", state_repr="sa", solved=True)
    b = Node(state_key="sb", state_repr="sb", solved=True)
    c = Node(state_key="sc", state_repr="sc", solved=True)
    root.add_child("exact long_tactic_name", a, 0.1)      # 2 tokens
    root.add_child("apply curriculum_target", b, 0.1)     # 2 tokens
    root.add_child("assumption", c, 0.1)                  # 1 token
    a.num_visit, b.num_visit, c.num_visit = 1, 9, 5
    a.num_evaluations = b.num_evaluations = c.num_evaluations = 1
    return root, a, b, c


def test_first_picks_insertion_order():
    root, a, b, c = make_root_with_three_solved()
    MCTS._mark_optimal(root, "first")
    assert a.is_optimal and not b.is_optimal and not c.is_optimal


def test_default_is_first():
    root, a, b, c = make_root_with_three_solved()
    MCTS._mark_optimal(root)
    assert a.is_optimal and not b.is_optimal and not c.is_optimal


def test_most_visited_picks_max_visits():
    root, a, b, c = make_root_with_three_solved()
    MCTS._mark_optimal(root, "most_visited")
    assert b.is_optimal and not a.is_optimal and not c.is_optimal


def test_shortest_action_picks_fewest_tokens():
    root, a, b, c = make_root_with_three_solved()
    MCTS._mark_optimal(root, "shortest_action")
    assert c.is_optimal and not a.is_optimal and not b.is_optimal


def test_search_uses_config_knob():
    """search() 应读取 SearchConfig.optimal_child_policy 并仍在 mock 上解出。"""
    env = PellMockEnv()
    cfg = SearchConfig(optimal_child_policy="shortest_action")
    mcts = MCTS(env=env, policy=mock_policy, value=mock_value, config=cfg)
    result = mcts.search("pell.root")
    assert result.solved
    assert result.best_script, "解出后应有证明脚本"
    # attempts 记录被填充（至少包含 accepted 与 env_rejected）
    kinds = {a["kind"] for a in mcts.attempts}
    assert "accepted" in kinds
    assert "env_rejected" in kinds

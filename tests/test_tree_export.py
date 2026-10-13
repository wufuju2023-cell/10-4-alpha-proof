"""E5/G1 前置：搜索树完整导出（schema + 尝试记录 + 原子写）。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaproof.config import SearchConfig  # noqa: E402
from alphaproof.data.tree_export import (SCHEMA, export_search_tree,  # noqa: E402
                                         write_search_tree)
from alphaproof.env.pell_mock import PellMockEnv, mock_policy, mock_value  # noqa: E402
from alphaproof.mcts.search import MCTS  # noqa: E402


def run_mock():
    env = PellMockEnv()
    mcts = MCTS(env=env, policy=mock_policy, value=mock_value, config=SearchConfig())
    result = mcts.search("pell.root")
    return mcts, result


def test_export_structure_and_invariants():
    mcts, result = run_mock()
    assert result.solved
    tree = export_search_tree(result.root, attempts=mcts.attempts, config=mcts.config)

    assert tree["schema"] == SCHEMA
    assert tree["counts"]["nodes"] == len(tree["nodes"]) >= 2
    assert tree["counts"]["edges"] == len(tree["edges"]) == len(tree["nodes"]) - 1
    assert tree["counts"]["attempts"] == len(tree["attempts"]) > 0

    # 根节点无父；其余节点恰有一条父边
    parents = {n["id"]: n["parent_id"] for n in tree["nodes"]}
    assert parents[0] is None
    for n in tree["nodes"][1:]:
        assert n["parent_id"] in parents

    # 边指向合法子节点；Q 与访问数一致
    for e in tree["edges"]:
        assert e["child_id"] in parents and parents[e["child_id"]] == e["parent_id"]
        if e["num_visit"]:
            assert abs(e["q"] - e["value"] / e["num_visit"]) < 1e-9

    # 解出树：存在 is_optimal 路径；mock 应包含 focus 边（AND 分支）
    assert any(n["is_optimal"] for n in tree["nodes"])
    assert any(e["is_focus"] for e in tree["edges"])


def test_attempt_kinds_and_fields():
    mcts, result = run_mock()
    tree = export_search_tree(result.root, attempts=mcts.attempts, config=mcts.config)
    kinds = {a["kind"] for a in tree["attempts"]}
    assert "env_rejected" in kinds          # mock 含 sorry / 不适用战术
    assert "accepted" in kinds
    for a in tree["attempts"]:
        assert a["node_id"] is not None, "attempt 必须能映射回树节点"
        assert a["kind"] in {"accepted", "dedup_action", "dedup_state", "env_rejected"}
        if a["kind"] == "env_rejected":
            assert a["error"] and a["ok"] is False


def test_write_is_atomic(tmp_path: Path):
    mcts, result = run_mock()
    out = write_search_tree(result.root, tmp_path / "tree.json",
                            attempts=mcts.attempts, config=mcts.config)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["counts"]["nodes"] >= 2
    assert not (tmp_path / "tree.json.tmp").exists(), "临时文件应已被替换"


def test_config_recorded():
    mcts, result = run_mock()
    tree = export_search_tree(result.root, attempts=mcts.attempts, config=mcts.config)
    cfg = tree["config"]
    assert cfg["prior_temperature"] == 200.0
    assert cfg["c_and"] == 64.0
    assert cfg["optimal_child_policy"] == "first"

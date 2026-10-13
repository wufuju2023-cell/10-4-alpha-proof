"""展示渲染器测试：渲染不报错、关键字段齐全、无外部资源引用。

对应验收：tools/showcase_render.py + docs/showcase/example-pell.html。
本测试只用 mock 搜索生成的真实导出树，不依赖网络/浏览器。
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from alphaproof.config import SearchConfig  # noqa: E402
from alphaproof.data.tree_export import export_search_tree  # noqa: E402
from alphaproof.env.pell_mock import PellMockEnv, mock_policy, mock_value  # noqa: E402
from alphaproof.mcts.search import MCTS  # noqa: E402
import showcase_render  # noqa: E402


@pytest.fixture(scope="module")
def mock_tree():
    env = PellMockEnv()
    mcts = MCTS(env=env, policy=mock_policy, value=mock_value, config=SearchConfig())
    result = mcts.search("pell.root")
    assert result.solved
    tree = export_search_tree(result.root, attempts=mcts.attempts, config=mcts.config)
    return tree, result


@pytest.fixture(scope="module")
def rendered(mock_tree, tmp_path_factory):
    tree, _result = mock_tree
    tmp = tmp_path_factory.mktemp("showcase")
    tree_path = tmp / "tree.json"
    tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    out = tmp / "out.html"
    rc = showcase_render.main(["--tree", str(tree_path), "--out", str(out)])
    assert rc == 0
    return tree, out.read_text(encoding="utf-8")


def test_render_contains_key_fields(rendered):
    tree, page = rendered
    assert page.startswith("<!DOCTYPE html>")
    plain = html.unescape(page)

    # 战术文本（边=行动），含 focus 伪边（页面对 < > 做 HTML 转义后校验原文）
    for tactic in ["intro B", "refine <17, 12, ?_, ?_>", "norm_num", "focus_goal"]:
        assert tactic in plain

    # 页头节点的计数与导出 counts 一致
    m = re.search(r"节点</b>\s*(\d+)", page)
    assert m and int(m.group(1)) == tree["counts"]["nodes"]
    m = re.search(r"attempts</b>\s*(\d+)", page)
    assert m and int(m.group(1)) == tree["counts"]["attempts"]

    # attempts 明细：类型与失败原因
    assert "env_rejected" in page
    assert "forbidden tactic" in page
    assert "not applicable" in page

    # 最优路径高亮标记数量与导出一致
    assert "★ 最优" in page
    n_opt = sum(1 for n in tree["nodes"] if n.get("is_optimal"))
    assert page.count('data-optimal="1"') == n_opt


def test_no_external_resources(rendered):
    _tree, page = rendered
    assert not re.search(r"https?://", page), "页面不应引用任何 http(s) 资源"
    assert 'src="//' not in page
    assert "<link" not in page.lower()
    assert "@import" not in page


def test_job_and_result_header(mock_tree, tmp_path):
    tree, result = mock_tree
    job = {
        "job_id": "pell-mock-0001",
        "title": "佩尔题（mock 展示）",
        "target": "∀ B : ℕ, ∃ x y : ℕ, B < x ∧ B < y ∧ x ^ 2 = 2 * y ^ 2 + 1",
    }
    result_json = dict(result.summary())
    result_json["elapsed_s"] = 0.01
    tp = tmp_path / "tree.json"
    tp.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    jp = tmp_path / "job.json"
    jp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    rp = tmp_path / "result.json"
    rp.write_text(json.dumps(result_json, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.html"
    rc = showcase_render.main(["--tree", str(tp), "--job", str(jp),
                               "--result", str(rp), "--title", "自定义标题",
                               "--out", str(out)])
    assert rc == 0
    page = out.read_text(encoding="utf-8")
    plain = html.unescape(page)
    assert "自定义标题" in page
    assert "佩尔题（mock 展示）" in plain
    assert "∀ B" in plain and "&lt;" in page     # 题目原文进页面且被转义
    assert "solved</b> 是" in page                 # 结果摘要 chips
    for tactic in result.best_script:
        assert tactic in plain


def test_cli_missing_file_returns_error(tmp_path):
    out = tmp_path / "out.html"
    rc = showcase_render.main(["--tree", str(tmp_path / "nope.json"),
                               "--out", str(out)])
    assert rc == 1
    assert not out.exists()


def test_truncation_banner(mock_tree, tmp_path):
    tree, _result = mock_tree
    tp = tmp_path / "tree.json"
    tp.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "small.html"
    rc = showcase_render.main(["--tree", str(tp), "--out", str(out),
                               "--max-nodes", "3"])
    assert rc == 0
    page = out.read_text(encoding="utf-8")
    assert "仅展示" in page

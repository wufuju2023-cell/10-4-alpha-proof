#!/usr/bin/env python3
"""MCTS 搜索树 → 人类可读的单文件静态 HTML（展示渲染器原型）。

输入（字段对齐 ``alphaproof/data/tree_export.py`` 的 schema，缺失字段容错）:

- ``--tree``   : ``write_search_tree`` 导出的 JSON
                 （nodes / edges / attempts / schema / config / root_state_key / counts）；
- ``--job``    : 可选，题目侧信息（title/target/statement 等键名尽力识别，其余字段折叠展示）；
- ``--result`` : 可选，搜索摘要（solved/simulations/nodes_created/best_script 等，其余折叠展示）；
- ``--title``  : 可选，页面大标题（优先于 job 里的标题）。

渲染内容: 节点=状态（state_repr 截断）、边=战术（action，标签标注 prior / N / Q /
step_cost），节点标注 to_play / N / V / 评估次数；``is_optimal`` 最优路径高亮；
下方表格列出 attempts（含失败类型与原因）。输出为单文件 HTML: 内嵌 CSS + 原生 SVG +
少量内联交互脚本，不引用任何外部资源（无 CDN / 外链 JS / 外链字体），浏览器离线可看。

用法::

    python tools/showcase_render.py --tree tree.json [--job job.json] \\
        [--result result.json] [--title T] --out out.html
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any, Optional

__all__ = ["main", "render_html", "write_html", "normalize_tree", "derive_optimal_actions"]

SCHEMA_EXPECTED = "alphaproof.search_tree_export.v1"
GENERATOR = "tools/showcase_render.py"

# --- 画布几何（CSS 像素）---
NODE_W = 244          # 节点盒宽
NODE_H = 88           # 节点盒高
X_GAP = 48            # 相邻叶节点水平间距
Y_GAP = 116           # 层间垂直间距（给边标签留空间）
MARGIN = 36           # 画布四周留白

# --- 颜色（与页面 CSS 中的取色一致）---
COL_OPTIMAL = "#d97706"   # 最优路径（琥珀）
COL_FOCUS = "#a855f7"     # focus 伪边（紫）
COL_NORMAL = "#94a3b8"    # 普通边（灰）

ATTEMPTS_DISPLAY_LIMIT = 400


# --------------------------------------------------------------------------- #
# 文本小工具
# --------------------------------------------------------------------------- #
def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _disp_width(s: str) -> int:
    """近似显示宽度：CJK 记 2 列，其余 1 列。"""
    return sum(2 if _is_wide(ch) else 1 for ch in s)


def _clip(s: Any, max_cols: int) -> str:
    """单行截断（按显示宽度），超长补省略号。"""
    text = " ".join(str(s if s is not None else "").split())
    if _disp_width(text) <= max_cols:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        cw = 2 if _is_wide(ch) else 1
        if used + cw > max_cols - 1:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _wrap_lines(s: Any, max_cols: int = 30, max_lines: int = 2) -> list[str]:
    """按显示宽度折行（优先在空格断行），最多 max_lines 行。"""
    text = _clip(s, max_cols * max_lines)
    lines: list[str] = []
    rest = text
    for _ in range(max_lines - 1):
        if _disp_width(rest) <= max_cols:
            break
        used, cut = 0, 0
        for i, ch in enumerate(rest):
            cw = 2 if _is_wide(ch) else 1
            if used + cw > max_cols:
                break
            used += cw
            cut = i + 1
        piece = rest[:cut]
        sp = piece.rfind(" ")
        if sp >= max(2, max_cols // 3):
            cut = sp
        lines.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest:
        lines.append(rest)
    return lines[:max_lines]


def _fmt_num(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:.{nd}f}"


def _esc(x: Any) -> str:
    return _html.escape(str(x)) if x is not None else ""


# --------------------------------------------------------------------------- #
# 读取 / 规范化
# --------------------------------------------------------------------------- #
def load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_tree(raw: Any) -> dict:
    """接受导出 JSON（顶层字段或 meta 子对象两种布局）。"""
    if not isinstance(raw, dict):
        raise ValueError("tree 顶层必须是 JSON 对象")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in raw:
            return raw[key]
        return meta.get(key, default)

    nodes = pick("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("tree.nodes 缺失或为空")
    edges = pick("edges") or []
    attempts = pick("attempts") or []
    if not isinstance(edges, list):
        raise ValueError("tree.edges 必须是数组")
    if not isinstance(attempts, list):
        raise ValueError("tree.attempts 必须是数组")
    return {
        "schema": pick("schema", SCHEMA_EXPECTED),
        "config": pick("config"),
        "root_state_key": pick("root_state_key"),
        "counts": pick("counts") or {},
        "nodes": nodes,
        "edges": edges,
        "attempts": attempts,
    }


# --------------------------------------------------------------------------- #
# 布局（tidy tree：叶节点占槽位，父节点居中于子节点）
# --------------------------------------------------------------------------- #
class _Layout:
    __slots__ = ("by_id", "order", "children", "visible", "xs", "ys", "depth",
                 "width", "height", "root", "edgemap", "total", "truncated")

    def __init__(self) -> None:
        self.by_id: dict[Any, dict] = {}
        self.order: list[Any] = []
        self.children: dict[Any, list[Any]] = {}
        self.visible: set = set()
        self.xs: dict[Any, float] = {}
        self.ys: dict[Any, float] = {}
        self.depth: dict[Any, int] = {}
        self.width = 0.0
        self.height = 0.0
        self.root: Any = None
        self.edgemap: dict[tuple, dict] = {}
        self.total = 0
        self.truncated = False


def _layout(tree: dict, max_nodes: int) -> _Layout:
    lay = _Layout()
    nodes = tree["nodes"]
    lay.total = len(nodes)
    for n in nodes:
        lay.by_id[n["id"]] = n
    roots = [n["id"] for n in nodes if n.get("parent_id") is None]
    lay.root = roots[0] if roots else nodes[0]["id"]
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None:
            lay.children.setdefault(pid, []).append(n["id"])
    for e in tree["edges"]:
        if isinstance(e, dict):
            lay.edgemap[(e.get("parent_id"), e.get("child_id"))] = e

    # 可见节点（DFS 前序；超过 max_nodes 截断，仅从浅层取）
    stack: list[tuple[Any, int]] = [(lay.root, 0)]
    while stack:
        if len(lay.order) >= max(1, max_nodes):
            break
        nid, d = stack.pop()
        if nid in lay.depth:
            continue
        lay.depth[nid] = d
        lay.order.append(nid)
        kids = lay.children.get(nid, [])
        for c in reversed(kids):
            if c not in lay.depth:
                stack.append((c, d + 1))
    lay.visible = set(lay.order)
    lay.truncated = lay.total > len(lay.order)

    # x：后序，叶节点依序占槽，父节点居中
    leaf_idx = 0
    stack2: list[tuple[Any, int]] = [(lay.root, 0)]
    while stack2:
        nid, state = stack2.pop()
        kids = [c for c in lay.children.get(nid, []) if c in lay.visible]
        if state == 0:
            stack2.append((nid, 1))
            for c in reversed(kids):
                stack2.append((c, 0))
        else:
            if not kids:
                lay.xs[nid] = MARGIN + leaf_idx * (NODE_W + X_GAP)
                leaf_idx += 1
            else:
                lay.xs[nid] = (min(lay.xs[c] for c in kids) + max(lay.xs[c] for c in kids)) / 2.0

    for nid in lay.order:
        lay.ys[nid] = MARGIN + lay.depth[nid] * (NODE_H + Y_GAP)
    n_leaves = max(leaf_idx, 1)
    max_depth = max(lay.depth.values()) if lay.depth else 0
    lay.width = MARGIN * 2 + n_leaves * (NODE_W + X_GAP) - X_GAP
    lay.height = MARGIN * 2 + max_depth * (NODE_H + Y_GAP) + NODE_H
    return lay


# --------------------------------------------------------------------------- #
# SVG 绘制
# --------------------------------------------------------------------------- #
def _svg_defs() -> str:
    def marker(mid: str, color: str) -> str:
        return (
            f'<marker id="{mid}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" '
            f'markerHeight="8" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M 0 0 L 8 4 L 0 8 z" fill="{color}"></path></marker>'
        )

    return ("<defs>"
            + marker("arr-norm", COL_NORMAL)
            + marker("arr-opt", COL_OPTIMAL)
            + marker("arr-focus", COL_FOCUS)
            + "</defs>")


def _edge_of(lay: _Layout, n: dict, pid: Any) -> dict:
    e = lay.edgemap.get((pid, n["id"]))
    if e is not None:
        return e
    return {"action": n.get("parent_action"), "prior": None, "num_visit": None,
            "value": None, "step_cost": None, "q": None,
            "is_focus": str(n.get("parent_action") or "").startswith("focus_goal")}


def _render_edge(n: dict, pid: Any, lay: _Layout) -> str:
    e = _edge_of(lay, n, pid)
    action = e.get("action") if e.get("action") is not None else n.get("parent_action")
    action_s = str(action if action is not None else "")
    is_focus = bool(e.get("is_focus")) or action_s.startswith("focus_goal")
    parent = lay.by_id.get(pid, {})
    parent_optimal = bool(parent.get("is_optimal"))
    child_optimal = bool(n.get("is_optimal"))
    optimal = parent_optimal and child_optimal

    px = lay.xs[pid] + NODE_W / 2.0
    py = lay.ys[pid] + NODE_H
    cx = lay.xs[n["id"]] + NODE_W / 2.0
    cy = lay.ys[n["id"]]
    span = max(cy - py, 40.0)
    dy = min(60.0, span * 0.45)
    end_y = cy - 3.0
    d = (f"M {px:.1f} {py:.1f} C {px:.1f} {py + dy:.1f}, "
         f"{cx:.1f} {end_y - dy:.1f}, {cx:.1f} {end_y:.1f}")

    # 边样式：focus 伪边用紫色虚线；在最优路径上的 focus 边叠加琥珀色底
    paths: list[str] = []
    if is_focus and optimal:
        cls = "edge focus optimal"
        paths.append(f'<path d="{d}" fill="none" stroke="{COL_OPTIMAL}" stroke-width="5" '
                     f'stroke-opacity="0.55" stroke-linecap="round"></path>')
        paths.append(f'<path d="{d}" fill="none" stroke="{COL_FOCUS}" stroke-width="2" '
                     f'stroke-dasharray="6 5" marker-end="url(#arr-focus)" stroke-linecap="round"></path>')
    elif optimal:
        cls = "edge optimal"
        paths.append(f'<path d="{d}" fill="none" stroke="{COL_OPTIMAL}" stroke-width="2.8" '
                     f'marker-end="url(#arr-opt)" stroke-linecap="round"></path>')
    elif is_focus:
        cls = "edge focus"
        paths.append(f'<path d="{d}" fill="none" stroke="{COL_FOCUS}" stroke-width="1.8" '
                     f'stroke-dasharray="6 5" marker-end="url(#arr-focus)" stroke-linecap="round"></path>')
    else:
        cls = "edge normal"
        paths.append(f'<path d="{d}" fill="none" stroke="{COL_NORMAL}" stroke-width="1.6" '
                     f'marker-end="url(#arr-norm)" stroke-linecap="round"></path>')

    parts2 = [f"action={action_s or '—'}"]
    if e.get("prior") is not None:
        parts2.append(f"p={_fmt_num(e.get('prior'), 3)}")
    if e.get("num_visit") is not None:
        parts2.append(f"N={e.get('num_visit')}")
    if e.get("q") is not None:
        parts2.append(f"Q={_fmt_num(e.get('q'))}")
    if e.get("step_cost") not in (None, 0, 0.0):
        parts2.append(f"cost={_fmt_num(e.get('step_cost'), 1)}")
    title = f"边 {pid}→{n['id']} · " + " · ".join(parts2)

    line1 = _clip(action_s, 26) or "—"
    line2_tokens = []
    if e.get("prior") is not None:
        line2_tokens.append(f"p={_fmt_num(e.get('prior'), 3)}")
    if e.get("num_visit") is not None:
        line2_tokens.append(f"N={e.get('num_visit')}")
    if e.get("q") is not None:
        line2_tokens.append(f"Q={_fmt_num(e.get('q'))}")
    if is_focus and not line2_tokens:
        line2_tokens.append("focus")
    line2 = " · ".join(line2_tokens) or "focus" if is_focus else (" · ".join(line2_tokens) or "—")

    mx = (px + cx) / 2.0
    my = (py + cy) / 2.0
    bw = max(_disp_width(line1) * 6.3, _disp_width(line2) * 6.0) + 16
    bh = 38.0

    return (
        f'<g class="{cls}" data-p="{_esc(pid)}" data-c="{_esc(n["id"])}">'
        + "".join(paths)
        + f'<title>{_esc(title)}</title>'
        f'<g class="label">'
        f'<rect x="{mx - bw / 2:.1f}" y="{my - bh / 2:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="6" '
        f'fill="#ffffff" fill-opacity="0.94" stroke="#e2e8f0" stroke-width="1"></rect>'
        f'<text x="{mx:.1f}" y="{my - 2:.1f}" text-anchor="middle" font-size="11.5" fill="#334155">{_esc(line1)}</text>'
        f'<text x="{mx:.1f}" y="{my + 12:.1f}" text-anchor="middle" font-size="10.5" fill="#64748b">{_esc(line2)}</text>'
        f'</g></g>'
    )


def _render_node(n: dict, lay: _Layout) -> str:
    nid = n["id"]
    x, y = lay.xs[nid], lay.ys[nid]
    optimal = bool(n.get("is_optimal"))
    solved = bool(n.get("solved"))
    terminal = bool(n.get("terminal"))
    play = str(n.get("to_play") or "OR").upper()
    and_node = play == "AND"
    nvisit = int(n.get("num_visit") or 0)
    nev = n.get("num_evaluations") or 0
    value = n.get("value")

    if solved or terminal:
        fill, stroke, sw = "#e9f8ee", "#2f9e4f", 1.6
    elif not nvisit:
        fill, stroke, sw = "#f3f4f6", "#9ca3af", 1.4
    elif and_node:
        fill, stroke, sw = "#f6f0ff", "#8b5cf6", 1.6
    else:
        fill, stroke, sw = "#eef6ff", "#3b82f6", 1.6
    dash = ' stroke-dasharray="5 4"' if (not nvisit and not solved and not terminal) else ""
    if optimal:
        stroke, sw = COL_OPTIMAL, 3.0

    classes = ["node"]
    classes.append("and" if and_node else "or")
    if solved:
        classes.append("solved")
    if terminal:
        classes.append("terminal")
    if optimal:
        classes.append("optimal")
    if not nvisit and not solved and not terminal:
        classes.append("unvisited")

    if optimal:
        tag_right, tag_color = "★ 最优", "#b45309"
    elif solved or terminal:
        tag_right, tag_color = "✓ 已解", "#15803d"
    elif not nvisit:
        tag_right, tag_color = "未访问", "#6b7280"
    else:
        tag_right, tag_color = "", "#6b7280"
    play_color = "#7c3aed" if and_node else "#2563eb"

    v_txt = _fmt_num(value) if nvisit else "—"
    lines = _wrap_lines(n.get("state_repr"), max_cols=30, max_lines=2)
    while len(lines) < 2:
        lines.append("")

    pid = n.get("parent_id")
    e = _edge_of(lay, n, pid) if pid is not None else None
    action = (e or {}).get("action")
    prior = (e or {}).get("prior")
    tip_parts = [f"节点 #{nid} [{play}] solved={'是' if solved else '否'} optimal={'是' if optimal else '否'} terminal={'是' if terminal else '否'}"]
    tip_parts.append(f"N={nvisit} · V={_fmt_num(value)} · ev={nev} · net={_fmt_num(n.get('net_value'))} · value_target={_fmt_num(n.get('value_target'))}")
    tip_parts.append(f"state_key: {n.get('state_key')}")
    if action is not None:
        tip_parts.append(f"入边战术: {action}（prior={_fmt_num(prior, 3)}）")
    tip_parts.append("")
    tip_parts.append(str(n.get("state_repr") or ""))

    def _attr(name: str, val: str) -> str:
        return f' {name}="{_esc(val)}"'

    attrs = (
        _attr("id", f"node-{nid}")
        + _attr("data-node", nid)
        + _attr("data-parent", "" if pid is None else pid)
        + _attr("data-toplay", play)
        + _attr("data-n", nvisit)
        + _attr("data-v", v_txt)
        + _attr("data-ev", nev)
        + _attr("data-key", n.get("state_key") or "")
        + _attr("data-action", action if action is not None else "")
        + _attr("data-prior", _fmt_num(prior, 3) if prior is not None else "—")
        + _attr("data-optimal", "1" if optimal else "0")
        + _attr("data-solved", "1" if solved else "0")
    )
    return (
        f'<g class="{" ".join(classes)}"{attrs} transform="translate({x:.1f}, {y:.1f})">'
        f'<rect class="box" x="0" y="0" width="{NODE_W}" height="{NODE_H}" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}></rect>'
        f'<text x="10" y="21" font-size="11" font-weight="700" fill="{play_color}">{_esc(play)}</text>'
        f'<text x="{NODE_W - 10}" y="21" text-anchor="end" font-size="11" fill="{tag_color}">{_esc(tag_right)}</text>'
        f'<text x="10" y="46" font-size="12.5" fill="#1f2937">{_esc(lines[0])}</text>'
        f'<text x="10" y="64" font-size="12.5" fill="#1f2937">{_esc(lines[1])}</text>'
        f'<text x="10" y="79" font-size="11" fill="#64748b">'
        f'N={nvisit} · V={v_txt} · ev={nev}</text>'
        f'<title>{_esc(chr(10).join(str(t) for t in tip_parts))}</title>'
        f'</g>'
    )


def _render_svg(lay: _Layout) -> str:
    parts: list[str] = [
        f'<svg class="tree-svg" width="{lay.width:.0f}" height="{lay.height:.0f}" '
        f'viewBox="0 0 {lay.width:.0f} {lay.height:.0f}" role="img" aria-label="MCTS 搜索树">',
        _svg_defs(),
    ]
    for nid in lay.order:
        if nid == lay.root:
            continue
        n = lay.by_id[nid]
        pid = n.get("parent_id")
        if pid is None or pid not in lay.visible:
            continue
        parts.append(_render_edge(n, pid, lay))
    for nid in lay.order:
        parts.append(_render_node(lay.by_id[nid], lay))
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 页头 / 结果推导 / 各区块 HTML
# --------------------------------------------------------------------------- #
def derive_optimal_actions(tree: dict) -> list[str]:
    """从 is_optimal 路径推导证明脚本（OR 取第一个最优子；focus 伪边不计）。"""
    nodes = {n["id"]: n for n in tree.get("nodes") or []}
    children: dict[Any, list[Any]] = {}
    for n in tree.get("nodes") or []:
        pid = n.get("parent_id")
        if pid is not None:
            children.setdefault(pid, []).append(n["id"])
    roots = [n["id"] for n in tree.get("nodes") or [] if n.get("parent_id") is None]
    if not roots:
        return []
    actions: list[str] = []
    stack = [roots[0]]
    seen: set = set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        n = nodes.get(nid)
        if not n or n.get("terminal"):
            continue
        kids = [c for c in children.get(nid, []) if nodes.get(c, {}).get("is_optimal")]
        if not kids:
            continue
        if str(n.get("to_play") or "OR").upper() == "AND":
            stack.extend(reversed(kids))
        else:
            c = kids[0]
            a = nodes.get(c, {}).get("parent_action")
            if a and not str(a).startswith("focus_goal"):
                actions.append(str(a))
            stack.append(c)
    return actions


def _chips_html(chips: list[tuple[str, Any]]) -> str:
    spans: list[str] = []
    for label, value in chips:
        spans.append(f'<span class="chip"><b>{_esc(label)}</b> {_esc(value)}</span>')
    return '<div class="chips">' + "".join(spans) + "</div>"


def _kv_details(summary: str, d: dict) -> str:
    rows: list[str] = []
    for k in sorted(d.keys(), key=str):
        v = d[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        rows.append(f'<tr><td class="k">{_esc(k)}</td><td>{_esc(_clip(v, 160))}</td></tr>')
    if not rows:
        return ""
    return (f'<details class="raw"><summary>{_esc(summary)}</summary>'
            f'<table class="kv">{"".join(rows)}</table></details>')


def _as_dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {"value": x}


def _job_section(job: Any) -> str:
    d = _as_dict(job)
    title = d.get("title") or d.get("name") or d.get("job_id") or d.get("id")
    target_keys = ("target", "statement", "problem", "theorem", "goal", "prompt")
    target = next((d[k] for k in target_keys
                   if isinstance(d.get(k), str) and d.get(k).strip()), None)
    known = set(target_keys) | {"title", "name", "job_id", "id"}
    rest = {k: v for k, v in d.items() if k not in known}
    parts = ['<section class="card" id="job">', "<h2>题目 / Job</h2>"]
    if title:
        parts.append(f'<p class="job-title">{_esc(title)}</p>')
    if target:
        parts.append(f"<pre>{_esc(target)}</pre>")
    if rest:
        parts.append(_kv_details("job 其他字段", rest))
    if not title and not target and not rest:
        parts.append('<p class="dim">（job 为空）</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _script_list(script: list[str], source: str) -> str:
    if not script:
        return f'<p class="dim">无脚本（solved=否 或树中无 is_optimal 路径）。来源：{_esc(source)}</p>'
    items = "".join(f"<li><code>{_esc(a)}</code></li>" for a in script)
    return (f'<h3>最优证明脚本（{_esc(source)}）</h3>'
            f'<ol class="script">{items}</ol>')


def _result_section(result: Any, tree: dict) -> str:
    d = _as_dict(result)
    chips: list[tuple[str, Any]] = []
    for key in ("solved", "simulations", "nodes_created", "elapsed_s"):
        if key in d:
            if key == "solved":
                chips.append((key, "是 ✓" if d[key] else "否"))
            else:
                chips.append((key, d[key]))
    parts = ['<section class="card" id="result">', "<h2>结果摘要</h2>"]
    if chips:
        parts.append(_chips_html(chips))
    script = d.get("best_script") or d.get("script")
    if isinstance(script, list) and script:
        parts.append(_script_list([str(a) for a in script], "result.json"))
    else:
        derived = derive_optimal_actions(tree)
        parts.append(_script_list(derived, "从 is_optimal 路径推导"))
    known = {"solved", "simulations", "nodes_created", "elapsed_s", "best_script", "script"}
    rest = {k: v for k, v in d.items() if k not in known}
    if rest:
        parts.append(_kv_details("result.json 其他字段", rest))
    parts.append("</section>")
    return "\n".join(parts)


def _header_html(tree: dict, lay: _Layout, job: Any, result: Any, title: Optional[str]) -> str:
    doc_title = title
    if not doc_title and isinstance(job, dict):
        doc_title = job.get("title") or job.get("name")
    if not doc_title:
        doc_title = "MCTS 搜索轨迹展示"
    chips: list[tuple[str, Any]] = [
        ("schema", tree.get("schema")),
        ("节点", len(tree["nodes"])),
        ("边", len(tree["edges"])),
        ("attempts", len(tree["attempts"])),
    ]
    if lay.truncated:
        chips.append(("显示节点", len(lay.order)))
    if tree.get("root_state_key"):
        chips.append(("root", _clip(tree.get("root_state_key"), 40)))
    parts = [
        '<header class="hero">',
        f"<h1>{_esc(doc_title)}</h1>",
        '<p class="sub">搜索树静态导出 · 节点=状态 · 边=战术 · 单文件离线可看（无外部引用）</p>',
        _chips_html(chips),
        "</header>",
    ]
    if job is not None:
        parts.append(_job_section(job))
    if result is not None:
        parts.append(_result_section(result, tree))
    return "\n".join(parts)


_LEGEND = (
    '<div class="legend">'
    '<span><i class="sw" style="background:#eef6ff;border-color:#3b82f6"></i>OR 节点</span>'
    '<span><i class="sw" style="background:#f6f0ff;border-color:#8b5cf6"></i>AND 节点</span>'
    '<span><i class="sw" style="background:#e9f8ee;border-color:#2f9e4f"></i>已解 / 终端</span>'
    '<span><i class="sw" style="background:#fff;border-color:#d97706"></i>最优路径（★ / 橙边）</span>'
    '<span><i class="sw" style="background:#f3f4f6;border-color:#9ca3af;border-style:dashed"></i>未访问</span>'
    '<span><i class="sw" style="background:#fff;border-color:#a855f7;border-style:dashed"></i>focus 伪边</span>'
    "</div>"
)


def _tree_section(tree: dict, lay: _Layout) -> str:
    parts = ['<section class="card" id="tree">',
             "<h2>搜索树（节点 = 状态 · 边 = 战术）</h2>"]
    if lay.truncated:
        parts.append(
            f'<p class="warn-banner">树较大：仅展示前 {len(lay.order)} 个节点'
            f'（共 {lay.total} 个）。完整数据见源 JSON。</p>')
    parts.append(_LEGEND)
    parts.append(
        '<div class="toolbar">'
        '<button type="button" data-zoom-btn="fit" class="active">适应窗口</button>'
        '<button type="button" data-zoom-btn="full">原始大小</button>'
        '<span class="dim">悬停节点高亮到根路径；点击查看完整状态</span>'
        "</div>")
    parts.append('<div class="canvas-wrap" data-zoom="fit">')
    parts.append(_render_svg(lay))
    parts.append("</div>")
    parts.append('<div class="detail-box" id="detail">点击节点查看完整状态（state_repr）与统计；'
                 "悬停可高亮祖先路径。</div>")
    parts.append("</section>")
    return "\n".join(parts)


_KIND_CLASS = {"accepted": "ok", "dedup_action": "warn", "dedup_state": "info",
               "env_rejected": "bad"}


def _attempts_section(tree: dict, lay: _Layout) -> str:
    attempts = tree["attempts"]
    kind_counts: dict[str, int] = {}
    for a in attempts:
        k = str(a.get("kind") or "?")
        kind_counts[k] = kind_counts.get(k, 0) + 1
    chips = "".join(
        f'<span class="badge {_KIND_CLASS.get(k, "other")}">{_esc(k)} {v}</span> '
        for k, v in sorted(kind_counts.items()))
    parts = ['<section class="card" id="attempts">',
             f"<h2>尝试记录（attempts，共 {len(attempts)} 条）</h2>",
             f'<div class="chips">{chips}</div>']

    shown = attempts[:ATTEMPTS_DISPLAY_LIMIT]
    rows: list[str] = []
    for a in shown:
        nid = a.get("node_id")
        n = lay.by_id.get(nid) if nid is not None else None
        if nid is None:
            node_cell = "—"
        else:
            node_cell = f'<a href="#node-{_esc(nid)}" data-jump="{_esc(nid)}">#{_esc(nid)}</a>'
            if n is not None:
                node_cell += (f' <span class="dim" title="{_esc(n.get("state_repr") or "")}">'
                              f'{_esc(_clip(n.get("state_repr"), 34))}</span>')
        kind = str(a.get("kind") or "?")
        kcls = _KIND_CLASS.get(kind, "other")
        ok = a.get("ok")
        ok_txt = "✓" if ok is True else ("✗" if ok is False else "—")
        err = a.get("error")
        err_cell = f'<span class="err">{_esc(err)}</span>' if err else '<span class="dim">—</span>'
        tr_attr = f' data-node="{_esc(nid)}"' if nid is not None else ""
        rows.append(
            f"<tr{tr_attr}>"
            f'<td>{_esc(a.get("seq"))}</td>'
            f"<td>{node_cell}</td>"
            f'<td><code>{_esc(a.get("action"))}</code></td>'
            f'<td><span class="badge {kcls}">{_esc(kind)}</span></td>'
            f"<td>{ok_txt}</td>"
            f"<td>{err_cell}</td>"
            "</tr>")
    if len(attempts) > len(shown):
        rows.append(f'<tr><td colspan="6" class="dim">… 另有 {len(attempts) - len(shown)} 条'
                     "未展示（超出页面显示上限）。</td></tr>")
    parts.append(
        '<table class="attempts"><thead><tr>'
        "<th>seq</th><th>节点</th><th>战术 action</th><th>类型 kind</th><th>ok</th><th>说明 / 错误</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    parts.append("</section>")
    return "\n".join(parts)


def _config_section(tree: dict, lay: _Layout) -> str:
    rows: list[str] = []
    cfg = tree.get("config")
    counts = tree.get("counts") or {}
    meta_rows = [
        ("schema", tree.get("schema")),
        ("root_state_key", tree.get("root_state_key")),
        ("counts.nodes（文件）", counts.get("nodes")),
        ("counts.edges（文件）", counts.get("edges")),
        ("counts.attempts（文件）", counts.get("attempts")),
        ("页面统计", f"节点 {len(tree['nodes'])} · 边 {len(tree['edges'])} · attempts {len(tree['attempts'])}"),
        ("显示上限", f"{len(lay.order)} / {lay.total}" + ("（已截断）" if lay.truncated else "")),
    ]
    for k, v in meta_rows:
        rows.append(f'<tr><td class="k">{_esc(k)}</td><td>{_esc(v)}</td></tr>')
    cfg_rows: list[str] = []
    if isinstance(cfg, dict):
        for k in sorted(cfg.keys(), key=str):
            cfg_rows.append(f'<tr><td class="k">{_esc(k)}</td><td>{_esc(cfg[k])}</td></tr>')
    parts = ['<details class="card raw" id="meta">',
             "<summary>搜索配置 / 元数据</summary>",
             f'<table class="kv">{"".join(rows)}</table>']
    if cfg_rows:
        parts.append("<h3>config</h3>")
        parts.append(f'<table class="kv">{"".join(cfg_rows)}</table>')
    parts.append("</details>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 页面组装
# --------------------------------------------------------------------------- #
_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: #f5f6f8;
  color: #1f2937;
  font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans CJK SC",
               "Source Han Sans SC", "PingFang SC", "Hiragino Sans GB",
               "Microsoft YaHei", sans-serif;
  font-size: 15px;
  line-height: 1.55;
}
.page { max-width: 1240px; margin: 0 auto; padding: 24px 20px 48px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 0 0 10px; }
h3 { font-size: 14px; margin: 14px 0 6px; color: #374151; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
.dim { color: #6b7280; }
.err { color: #b91c1c; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 16px 18px; margin: 14px 0; }
.hero { padding: 6px 2px 0; }
.sub { color: #6b7280; font-size: 13px; margin: 0 0 4px; }
.job-title { margin: 0 0 8px; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 2px; align-items: center; }
.chip { background: #eef2f7; border: 1px solid #dbe2ea; border-radius: 999px;
        padding: 2px 10px; font-size: 12.5px; }
.chip b { color: #475569; font-weight: 600; margin-right: 4px; }
pre { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px;
      padding: 10px 12px; overflow: auto; font-size: 13px; margin: 8px 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
                   "Noto Sans Mono CJK SC", monospace; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
                   "Noto Sans Mono CJK SC", monospace; font-size: 12.5px; }
ol.script li { margin: 2px 0; }
.canvas-wrap { overflow: auto; border: 1px dashed #d1d5db; border-radius: 10px;
               background: #fbfcfe; }
.canvas-wrap[data-zoom="fit"] .tree-svg { max-width: 100%; height: auto; display: block; }
.canvas-wrap[data-zoom="full"] .tree-svg { max-width: none; display: block; }
svg text { font-family: inherit; }
.node { cursor: pointer; }
.node.hl .box { stroke: #e11d48 !important; stroke-width: 3.4 !important; }
.edge.hl path { stroke: #e11d48 !important; stroke-width: 3.4 !important; }
.edge.hl .label rect { stroke: #e11d48; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12.5px;
          color: #475569; margin: 4px 0 10px; align-items: center; }
.sw { display: inline-block; width: 14px; height: 14px; border-radius: 4px;
      border: 2px solid #888; margin-right: 4px; vertical-align: -2px; }
.toolbar { display: flex; gap: 8px; align-items: center; margin: 4px 0 8px;
           flex-wrap: wrap; }
.toolbar button { border: 1px solid #d1d5db; background: #fff; border-radius: 8px;
                  padding: 3px 10px; font-size: 12.5px; cursor: pointer; }
.toolbar button.active { background: #2563eb; color: #fff; border-color: #2563eb; }
.warn-banner { background: #fef3c7; border: 1px solid #fcd34d; color: #92400e;
               border-radius: 8px; padding: 6px 10px; font-size: 13px; }
table.attempts { width: 100%; border-collapse: collapse; font-size: 13px; }
table.attempts th, table.attempts td { border-bottom: 1px solid #eef1f4;
                                       padding: 6px 8px; text-align: left;
                                       vertical-align: top; }
table.attempts th { background: #f8fafc; font-weight: 600; color: #334155; }
table.attempts tr[data-node] { cursor: pointer; }
table.attempts tr[data-node]:hover { background: #fff7ed; }
table.kv { border-collapse: collapse; font-size: 13px; width: 100%; }
table.kv td { border-bottom: 1px solid #eef1f4; padding: 4px 8px; vertical-align: top; }
table.kv td.k { color: #475569; white-space: nowrap; width: 220px; }
.badge { display: inline-block; border-radius: 6px; padding: 0 7px; font-size: 12px;
         line-height: 19px; border: 1px solid transparent; }
.badge.ok { background: #e9f8ee; color: #166534; border-color: #bbf7d0; }
.badge.warn { background: #fef9c3; color: #854d0e; border-color: #fde68a; }
.badge.info { background: #e0f2fe; color: #075985; border-color: #bae6fd; }
.badge.bad { background: #fee2e2; color: #991b1b; border-color: #fecaca; }
.badge.other { background: #f1f5f9; color: #475569; border-color: #e2e8f0; }
.detail-box { margin-top: 10px; background: #f8fafc; border: 1px solid #e2e8f0;
              border-radius: 8px; padding: 10px 12px; font-size: 12.5px;
              white-space: pre-wrap; word-break: break-word; min-height: 60px;
              font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
                           "Noto Sans Mono CJK SC", monospace; }
details.raw summary { cursor: pointer; color: #334155; font-weight: 600; }
details.card.raw summary { font-size: 15px; }
footer { color: #6b7280; font-size: 12.5px; margin-top: 20px; text-align: center; }
"""


_JS = r"""
(function () {
  "use strict";
  var nodes = {};
  var edges = {};
  document.querySelectorAll("g.node").forEach(function (g) {
    nodes[String(g.dataset.node)] = g;
  });
  document.querySelectorAll("g.edge").forEach(function (g) {
    edges[String(g.dataset.p) + "|" + String(g.dataset.c)] = g;
  });
  function clearHL() {
    document.querySelectorAll(".hl").forEach(function (el) { el.classList.remove("hl"); });
  }
  function highlight(id) {
    clearHL();
    var cur = String(id);
    var guard = 0;
    while (nodes[cur] && guard < 100000) {
      nodes[cur].classList.add("hl");
      var p = nodes[cur].dataset.parent;
      if (p === undefined || p === null || p === "" || p === "None") { break; }
      var e = edges[String(p) + "|" + cur];
      if (e) { e.classList.add("hl"); }
      cur = String(p);
      guard += 1;
    }
  }
  function showDetail(g) {
    var box = document.getElementById("detail");
    if (!box) { return; }
    var t = g.querySelector("title");
    var lines = [];
    lines.push("节点 #" + g.dataset.node + " · " + g.dataset.toplay +
               (g.dataset.optimal === "1" ? " · ★ 最优路径" : "") +
               (g.dataset.solved === "1" ? " · 已解" : ""));
    lines.push("N(访问)=" + g.dataset.n + "  V(价值)=" + g.dataset.v +
               "  ev(评估)=" + g.dataset.ev);
    lines.push("state_key: " + g.dataset.key);
    if (g.dataset.action) {
      lines.push("入边战术: " + g.dataset.action + "  (prior=" + g.dataset.prior + ")");
    }
    lines.push("");
    lines.push(t ? t.textContent : "");
    box.textContent = lines.join("\n");
  }
  document.querySelectorAll("g.node").forEach(function (g) {
    g.addEventListener("mouseenter", function () { highlight(g.dataset.node); });
    g.addEventListener("mouseleave", clearHL);
    g.addEventListener("click", function () { highlight(g.dataset.node); showDetail(g); });
  });
  document.querySelectorAll("tr[data-node]").forEach(function (row) {
    row.addEventListener("mouseenter", function () { highlight(row.dataset.node); });
    row.addEventListener("mouseleave", clearHL);
    row.addEventListener("click", function () {
      var g = nodes[String(row.dataset.node)];
      if (!g) { return; }
      highlight(row.dataset.node);
      showDetail(g);
      g.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  document.querySelectorAll("[data-jump]").forEach(function (a) {
    a.addEventListener("click", function (ev) {
      ev.preventDefault();
      var g = nodes[String(a.dataset.jump)];
      if (!g) { return; }
      highlight(a.dataset.jump);
      showDetail(g);
      g.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  document.querySelectorAll("[data-zoom-btn]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var wrap = document.querySelector(".canvas-wrap");
      if (wrap) { wrap.dataset.zoom = btn.dataset.zoomBtn; }
      document.querySelectorAll("[data-zoom-btn]").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
    });
  });
})();
"""


def render_html(tree: dict, job: Any = None, result: Any = None,
                title: Optional[str] = None, max_nodes: int = 3000) -> str:
    """把导出树渲染为完整单文件 HTML（字符串）。"""
    t = normalize_tree(tree)
    lay = _layout(t, max_nodes=max_nodes)
    doc_title = title
    if not doc_title and isinstance(job, dict):
        doc_title = job.get("title") or job.get("name")
    doc_title = doc_title or "MCTS 搜索轨迹展示"
    sections = [
        _header_html(t, lay, job, result, title),
        _tree_section(t, lay),
        _attempts_section(t, lay),
        _config_section(t, lay),
    ]
    footer = (f'<footer>由 {_esc(GENERATOR)} 生成 · schema {_esc(t.get("schema"))} · '
              "单文件离线 HTML（无外部引用）</footer>")
    page = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(doc_title)}</title>",
        "<style>",
        _CSS.strip(),
        "</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        *sections,
        footer,
        "</main>",
        "<script>",
        _JS.strip(),
        "</script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(page) + "\n"


def write_html(page: str, path: str | Path) -> Path:
    """原子写（临时文件 + os.replace），返回落盘路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, p)
    return p


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="showcase_render.py",
        description="MCTS 搜索树 → 单文件静态 HTML（无外部依赖）")
    ap.add_argument("--tree", required=True, help="tree_export 导出的树 JSON")
    ap.add_argument("--job", default=None, help="可选：题目 job JSON")
    ap.add_argument("--result", default=None, help="可选：搜索 result JSON")
    ap.add_argument("--title", default=None, help="可选：页面标题")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--max-nodes", type=int, default=3000,
                    help="画布最多渲染的节点数（默认 3000，超出截断并在页面标注）")
    args = ap.parse_args(argv)

    try:
        raw_tree = load_json(args.tree)
        tree = normalize_tree(raw_tree)
        job = load_json(args.job) if args.job else None
        result = load_json(args.result) if args.result else None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[showcase_render] 读取失败: {exc}", file=sys.stderr)
        return 1

    page = render_html(tree, job=job, result=result, title=args.title,
                       max_nodes=max(1, args.max_nodes))
    write_html(page, args.out)
    print(f"[showcase_render] 已写出 {args.out}（{len(page)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

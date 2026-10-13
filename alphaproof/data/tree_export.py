"""搜索树完整导出（E5 前置件：软目标 / 校准 / 逐候选尝试记录）。

背景：`reports/htps-selection-recheck.md` §6 发现 FATE 波次的 raw_tree 只保留
最优解出路径（2 节点、visits=0），访问分布软目标不可恢复。本模块给本机 MCTS
提供"完整树 + 尝试记录"的导出，字段对齐该报告的最小导出清单：

- nodes   ：state_key/state_repr、to_play、terminal/solved/is_optimal、
            num_visit/num_evaluations/value_sum/net_value/value_target/value；
- edges   ：action/prior/is_focus/num_visit/value/step_cost/Q；
- attempts：本次搜索中每个被尝试的 (node, tactic)：
            kind ∈ {accepted, dedup_action, dedup_state, env_rejected}（+error）；
- meta    ：schema、config、计数。

与真实波次的差距（需 runner/actor 侧补充，见报告 §6）：
逐候选 Lean 执行耗时（µs）、真实失败类别、每 attempt 的超参与模型版本。
本模块只负责本机管线（parity/mock）的同口径导出，作为补丁参考实现。

原子写：临时文件 + os.replace（对齐 `new-python-version-mcts-plan` 的存储约定）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ..mcts.tree import Node

SCHEMA = "alphaproof.search_tree_export.v1"


def export_search_tree(
    root: Node,
    attempts: Optional[list] = None,
    config: Any = None,
    state_repr_maxlen: int = 400,
    include_focus_edges: bool = True,
) -> dict:
    """把搜索树导出为可 JSON 化的 dict（不落盘）。"""
    node_ids: dict[int, int] = {}
    nodes: list[dict] = []
    edges: list[dict] = []

    def walk(node: Node, parent_id: Optional[int], parent_action: Optional[str]) -> None:
        nid = len(nodes)
        node_ids[id(node)] = nid
        nodes.append({
            "id": nid,
            "parent_id": parent_id,
            "parent_action": parent_action,
            "state_key": node.state_key,
            "state_repr": (node.state_repr or "")[:state_repr_maxlen],
            "to_play": node.to_play,
            "terminal": node.terminal,
            "solved": node.solved,
            "is_optimal": node.is_optimal,
            "num_visit": node.num_visit,
            "num_evaluations": node.num_evaluations,
            "value_sum": node.value_sum,
            "net_value": node.net_value,
            "value_target": node.value_target,
            "value": node.value(),
        })
        for action, child in node.children.items():
            if not include_focus_edges and action.startswith("focus_goal"):
                continue
            edge = node.edges.get(action)
            edge_out = {
                "parent_id": nid,
                "child_id": None,
                "action": action,
                "prior": edge.prior if edge else None,
                "is_focus": edge.is_focus if edge else action.startswith("focus_goal"),
                "num_visit": edge.num_visit if edge else None,
                "value": edge.value if edge else None,
                "step_cost": edge.step_cost if edge else None,
                "q": (edge.value / edge.num_visit) if (edge and edge.num_visit) else None,
            }
            edges.append(edge_out)
            walk(child, nid, action)
            edge_out["child_id"] = node_ids[id(child)]

    walk(root, None, None)

    attempts_out: list[dict] = []
    if attempts:
        for att in attempts:
            src_node = att.get("node")
            attempts_out.append({
                "seq": att.get("seq"),
                "node_id": node_ids.get(id(src_node)) if src_node is not None else None,
                "action": att.get("action"),
                "prior": att.get("prior"),
                "kind": att.get("kind"),
                "ok": att.get("ok"),
                "error": att.get("error"),
                "child_state_key": att.get("child_state_key"),
            })

    return {
        "schema": SCHEMA,
        "config": config.to_dict() if hasattr(config, "to_dict") else (config or None),
        "root_state_key": root.state_key,
        "counts": {"nodes": len(nodes), "edges": len(edges), "attempts": len(attempts_out)},
        "nodes": nodes,
        "edges": edges,
        "attempts": attempts_out,
    }


def write_search_tree(root: Node, path: str | Path, **kwargs: Any) -> Path:
    """原子写（临时文件 + os.replace），返回落盘路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = export_search_tree(root, **kwargs)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path

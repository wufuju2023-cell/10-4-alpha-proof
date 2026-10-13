"""Transition JSONL 读写（训练池 / 事件流 / 回执）。

- 读取：目录（``shard-*.jsonl`` 优先，否则 ``*.jsonl``）或单个文件；
- 写入：临时文件 + ``os.replace`` 原子替换（对齐 store 约定，05 §1）。

只依赖 ``alphaproof.data.events.Transition``（schema 冻结件），不定义新 schema。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, List

from alphaproof.data.events import Transition


def resolve_pool_files(path: str | os.PathLike[str]) -> List[Path]:
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("shard-*.jsonl"))
        if not files:
            files = sorted(p.glob("*.jsonl"))
        if not files:
            raise SystemExit(f"pool 目录里没有 *.jsonl: {p}")
        return files
    if p.is_file():
        return [p]
    raise SystemExit(f"pool 不存在: {p}")


def load_pool(path: str | os.PathLike[str]) -> List[Transition]:
    """加载 Transition JSONL；行解析失败直接报错（避免静默丢样本）。"""
    out: List[Transition] = []
    for file in resolve_pool_files(path):
        with file.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Transition.from_dict(json.loads(line)))
                except Exception as exc:  # noqa: BLE001 - 报错带行号
                    raise SystemExit(f"{file}:{lineno} 解析失败: {exc}") from exc
    return out


def write_jsonl(path: str | os.PathLike[str], records: Iterable[Any]) -> None:
    """原子写 JSONL（records 需支持 json.dumps；dataclass 先 to_dict）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            if hasattr(record, "to_dict"):
                record = record.to_dict()
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def write_json(path: str | os.PathLike[str], obj: Any) -> None:
    """原子写单个 JSON（报告等）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))
    os.replace(tmp, p)


def kind_counts(transitions: Iterable[Transition]) -> dict:
    counts: dict = {}
    for t in transitions:
        counts[t.kind] = counts.get(t.kind, 0) + 1
    return counts

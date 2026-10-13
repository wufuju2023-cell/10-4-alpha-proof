#!/usr/bin/env python3
"""E1 降级版：历史语义多样性审计（纯 CPU，0 GPU）。

E1 完整版需要 GPU 重新采样（K=32/64 × 温度 ∈ {0.7,1.0}）；本脚本实现实验计划里的
"无 GPU 降级"：直接解析 evidence 里已有的 `policy_requests` + `observer`，对
20 题（每题 64 个样本）计算历史近似指标：

  D_sem_hist = #canonical / 64           语义去重后的候选率（近似"不同状态/结果"率）
  D_raw      = #unique 原始文本 / 64      原始文本多样率（未规范化）
  D_fam_hist = #unique 族头 / #canonical  族头 = 动作首个空白分隔 token（intro/simp/...）
  top1_share = max(k) / 64                模态塌缩指标（最高频变体占比）
  entropy    = 频率分布的 Shannon 熵（bit）
  exec_rate  = 执行候选 / canonical
  verify_rate= strict verified / canonical

数据口径与 E2 完全一致（`E2_WORK` 可改；幂等）。输出 JSON 默认
`$E2_WORK/e1-diversity-audit.json`，stdout 打表。

局限（报告里原样带上）：单波、单一温度 1.5、tier-1 单步题；D_sem 是
"canonical 动作去重"近似（不是真实 state_key 去重），只适合与同口径数字比较。
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
from pathlib import Path

E2 = Path(os.environ.get("E2_WORK", "~/e2-work")).expanduser()
TRAIN = E2 / "training/remote/tmp/fate-m-formal-w001-shared-rollout-v2"
B = 64


def iter_problems():
    return sorted(d.name for d in TRAIN.iterdir() if d.name.startswith("fate_m_"))


def load_problem(p):
    reqs = sorted(glob.glob(str(TRAIN / p / "actor_receipts" / "*" / "policy_requests" / "*.json")))
    assert len(reqs) == 1, (p, len(reqs))
    cands = json.loads(Path(reqs[0]).read_text())["candidates"]
    raw_texts = [c["returned_text"] for c in cands]

    canon, results = {}, {}
    for line in (TRAIN / p / "observer.jsonl").read_text().splitlines():
        ev = json.loads(line)
        kind = ev.get("kind")
        if kind == "canonical_candidate":
            canon[ev["candidate_index"]] = ev
        elif kind == "canonical_candidate_result":
            results[ev["candidate_index"]] = ev

    rows = []
    for ci, ev in canon.items():
        res = results.get(ci, {})
        rows.append({
            "action": ev["action"],
            "k": len(ev["sample_indices"]),
            "head": ev["action"].split()[0] if ev["action"].split() else "",
            "executed": bool(res.get("did_execute")),
            "verified": res.get("verifier_status") == "verified_proof",
        })
    return raw_texts, rows


def shannon_bits(counts: list[int], total: int) -> float:
    ent = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        ent -= p * math.log2(p)
    return ent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(E2 / "e1-diversity-audit.json"))
    args = ap.parse_args()

    problems = iter_problems()
    per_problem = []
    for p in problems:
        raw_texts, rows = load_problem(p)
        k_list = [r["k"] for r in rows]
        heads = [r["head"] for r in rows]
        d_sem = len(rows) / B
        d_raw = len(set(raw_texts)) / B
        d_fam = len(set(heads)) / len(rows) if rows else 0.0
        per_problem.append({
            "problem": p,
            "n_canonical": len(rows),
            "d_sem_hist": d_sem,
            "d_raw": d_raw,
            "n_heads": len(set(heads)),
            "d_fam_hist": d_fam,
            "top1_share": max(k_list) / B if k_list else 0.0,
            "entropy_bits": shannon_bits(k_list, B),
            "exec_rate": sum(1 for r in rows if r["executed"]) / len(rows) if rows else 0.0,
            "verify_rate": sum(1 for r in rows if r["verified"]) / len(rows) if rows else 0.0,
            "n_verified": sum(1 for r in rows if r["verified"]),
            "head_hist": dict(collections.Counter(heads).most_common()),
        })

    def agg(key):
        vals = [pp[key] for pp in per_problem]
        return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)}

    summary = {
        "n_problems": len(problems),
        "budget": B,
        "note": "degraded historical approximation (single wave, T=1.5, tier-1)",
        "aggregate": {k: agg(k) for k in
                      ("d_sem_hist", "d_raw", "d_fam_hist", "top1_share",
                       "entropy_bits", "exec_rate", "verify_rate")},
        "totals": {
            "raw": sum(len(load_problem(p)[0]) for p in problems),
            "canonical": sum(pp["n_canonical"] for pp in per_problem),
            "verified": sum(pp["n_verified"] for pp in per_problem),
        },
        "per_problem": per_problem,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"E1 降级审计：{len(problems)} 题 / B={B}")
    print(f"{'problem':>22} {'D_sem':>6} {'D_raw':>6} {'D_fam':>6} {'top1':>6} {'H(bit)':>7} {'exec':>5} {'ver':>5}")
    for pp in per_problem:
        print(f"{pp['problem']:>22} {pp['d_sem_hist']:>6.3f} {pp['d_raw']:>6.3f} {pp['d_fam_hist']:>6.3f} "
              f"{pp['top1_share']:>6.3f} {pp['entropy_bits']:>7.3f} {pp['exec_rate']:>5.2f} {pp['verify_rate']:>5.2f}")
    print()
    for key, val in summary["aggregate"].items():
        print(f"  {key:>12}: mean={val['mean']:.3f}  [{val['min']:.3f}, {val['max']:.3f}]")
    print()
    print("saved:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

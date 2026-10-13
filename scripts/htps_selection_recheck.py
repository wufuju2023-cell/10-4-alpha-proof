#!/usr/bin/env python3
"""HTPS 式"最小证明选择"重排实验（纯 CPU / 离线，0 GPU）。

背景
----
HTPS（arXiv 2205.11491）训练策略用的样本来自**成功搜索的最小证明树**
（Metamath/Equations 按步数最小；Lean 按战术总 CPU 时间最小），
消融显示 "minimal proofs ≫ all proofs"（78.1% vs 40.6%）。
我们当前波次式 CE 的选择规则是 "first-verified"（E2 实测：评估顺序最早通过验证，
顺序敏感，20 条 selected 中 11 条 k≤2）。

本脚本用现有证据（20 题 / 49 条 verified canonical 候选 / 每题 64 次采样）**离线重放**
不同选择规则，量化：
  1. 规则换成"最高频"或"最短战术"后，选中变体的 k 分布如何变化；
  2. 期望重发现概率（同预算再跑一波还能抽到选中变体的概率）；
  3. 规则之间选出的动作有多少不同（目标噪声）；
  4. token 长度与命中次数 k 的相关性（"最短=更常见"是否成立）。

规则
----
  R0 first   : 评估顺序最早通过验证（现状，E2 观测规则）
  R1 maxk    : k 最大（tie: 顺序早）
  R2 mintok  : 战术 token 数最少（tie: k 大，再按顺序）—— "最小证明"代理
  R3 minchar : 动作字符数最少（tie: k 大，再按顺序）—— 稳健性对照
  RAND       : 均匀随机（解析期望值）

口径与来源（与 reports/e2-window-audit.md 相同）
  - 数据：~/e2-work（E2_WORK 可改），由证据包解压；
  - k = canonical.sample_indices 长度（64 次采样中的命中次数）；
  - token 数 = canonical.sample_tactic_token_spans 中 survivor 样本的 [start,end) 跨度；
  - 重发现概率 ≈ 1-(1-k/64)^64（MLE 近似）；
  - "多解题" = 该题 verified canonical 数 ≥ 2 的题（本次 9 题，全部题 20 题）。

输出
----
  stdout 表格 + JSON：默认 $E2_WORK/htps-selection-recheck.json（可用 --out 改）。
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
LOG10 = math.log(10)


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def iter_problems():
    return sorted(d.name for d in TRAIN.iterdir() if d.name.startswith("fate_m_"))


def load_request(p):
    reqs = sorted(glob.glob(str(TRAIN / p / "actor_receipts" / "*" / "policy_requests" / "*.json")))
    assert len(reqs) == 1, (p, len(reqs))
    return json.loads(Path(reqs[0]).read_text())


def load_problem_ext(p):
    """返回 (rows, selected_event)。rows 每条 canonical 候选含 token/char 长度与顺序。"""
    cands = load_request(p)["candidates"]
    canon, results, selected = {}, {}, None
    for line in (TRAIN / p / "observer.jsonl").read_text().splitlines():
        ev = json.loads(line)
        kind = ev.get("kind")
        if kind == "canonical_candidate":
            canon[ev["candidate_index"]] = ev
        elif kind == "canonical_candidate_result":
            results[ev["candidate_index"]] = ev
        elif kind == "canonical_selected_path":
            selected = ev
    sel_ids = set(selected.get("selected_event_ids", [])) if selected else set()

    rows = []
    for ci, ev in canon.items():
        res = results.get(ci, {})
        si = ev["survivor_sample_index"]
        idx = ev["sample_indices"]
        spans = ev.get("sample_tactic_token_spans") or []
        # 校验 span 结构；取 survivor 样本的跨度
        tok_len = None
        if spans and len(spans) == len(idx):
            pos = idx.index(si)
            span = spans[pos]
            if isinstance(span, list) and len(span) == 2:
                tok_len = int(span[1]) - int(span[0])
        rc = cands[si]
        rows.append({
            "problem": p,
            "ci": ci,
            "action": ev["action"],
            "k": len(idx),
            "survivor": si,
            "tok_len": tok_len,
            "char_len": len(ev["action"]),
            "logp_old": sum(rc["raw_completion_old_logprobs"]),
            "status": res.get("verifier_status"),
            "selected": ev.get("event_id") in sel_ids,
            "sequence": int(ev.get("sequence", 0)),
            "event_id": ev.get("event_id"),
        })
    return rows, selected


# --------------------------------------------------------------------------- #
# 选择规则
# --------------------------------------------------------------------------- #
def pick(rows, rule):
    """rows 为某题的 verified 行；返回该规则选中的行。"""
    if rule == "first":
        return min(rows, key=lambda r: r["sequence"])
    if rule == "maxk":
        return max(rows, key=lambda r: (r["k"], -r["sequence"]))
    if rule == "mintok":
        return min(rows, key=lambda r: (r["tok_len"] if r["tok_len"] is not None else 10**9,
                                        -r["k"], r["sequence"]))
    if rule == "minchar":
        return min(rows, key=lambda r: (r["char_len"], -r["k"], r["sequence"]))
    raise ValueError(rule)


def p_rediscover(k):
    return 1 - (1 - k / B) ** B


# --------------------------------------------------------------------------- #
# 统计工具
# --------------------------------------------------------------------------- #
def spearman(xs, ys):
    def rank(a):
        order = sorted(range(len(a)), key=lambda i: a[i])
        r = [0.0] * len(a)
        i = 0
        while i < len(a):
            j = i
            while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for t in range(i, j + 1):
                r[order[t]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy)


def fmt_row(r):
    tok = r["tok_len"] if r["tok_len"] is not None else "?"
    return f"k={r['k']:>2} tok={tok:>2} seq={r['sequence']:>3} {r['action'][:52]!r}"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(E2 / "htps-selection-recheck.json"))
    args = ap.parse_args()

    problems = iter_problems()
    rows_by_p = {}
    for p in problems:
        rows, _ = load_problem_ext(p)
        rows_by_p[p] = rows

    # 校验 span 数据完整性
    missing = [(r["problem"], r["ci"]) for rows in rows_by_p.values() for r in rows
               if r["status"] == "verified_proof" and r["tok_len"] is None]
    if missing:
        print("警告：以下 verified 行缺 token span：", missing[:10], f"（共 {len(missing)}）")

    rules = ["first", "maxk", "mintok", "minchar"]
    per_problem = []
    for p in problems:
        ver = [r for r in rows_by_p[p] if r["status"] == "verified_proof"]
        picks = {ru: pick(ver, ru) for ru in rules}
        # 随机规则的解析期望
        mean_k = sum(r["k"] for r in ver) / len(ver)
        rand_k1 = sum(1 for r in ver if r["k"] == 1) / len(ver)
        per_problem.append({
            "problem": p,
            "n_verified": len(ver),
            "picks": {ru: {"action": picks[ru]["action"], "k": picks[ru]["k"],
                           "tok_len": picks[ru]["tok_len"], "seq": picks[ru]["sequence"]}
                      for ru in rules},
            "rand_expect": {"mean_k": mean_k, "p_k1": rand_k1,
                            "mean_rediscover": sum(p_rediscover(r["k"]) for r in ver) / len(ver)},
            "verified_k": sorted((r["k"] for r in ver), reverse=True),
        })

    # ---------- 汇总指标 ----------
    agg = {}
    for ru in rules:
        ks = [pp["picks"][ru]["k"] for pp in per_problem]
        agg[ru] = {
            "sum_k": sum(ks),
            "mean_k": sum(ks) / len(ks),
            "n_k1": sum(1 for k in ks if k == 1),
            "n_k_le2": sum(1 for k in ks if k <= 2),
            "mean_rediscover": sum(p_rediscover(k) for k in ks) / len(ks),
            "median_k": sorted(ks)[len(ks) // 2],
        }
    agg["RAND(期望)"] = {
        "sum_k": sum(pp["rand_expect"]["mean_k"] for pp in per_problem),
        "mean_k": sum(pp["rand_expect"]["mean_k"] for pp in per_problem) / len(per_problem),
        "n_k1": sum(pp["rand_expect"]["p_k1"] for pp in per_problem),
        "n_k_le2": None,
        "mean_rediscover": sum(pp["rand_expect"]["mean_rediscover"] for pp in per_problem) / len(per_problem),
        "median_k": None,
    }

    # 规则间动作差异（跨全部 20 题；等价于跨 9 个多解题）
    diff12 = sum(1 for pp in per_problem if pp["picks"]["first"]["action"] != pp["picks"]["maxk"]["action"])
    diff13 = sum(1 for pp in per_problem if pp["picks"]["first"]["action"] != pp["picks"]["mintok"]["action"])
    diff14 = sum(1 for pp in per_problem if pp["picks"]["first"]["action"] != pp["picks"]["minchar"]["action"])
    diff23 = sum(1 for pp in per_problem if pp["picks"]["maxk"]["action"] != pp["picks"]["mintok"]["action"])

    # ---------- token 长度 vs k 相关性（verified 集合） ----------
    ver_all = [r for rows in rows_by_p.values() for r in rows if r["status"] == "verified_proof"]
    inv_all = [r for rows in rows_by_p.values() for r in rows if r["status"] == "invalid_tactic"]
    xs = [r["tok_len"] for r in ver_all if r["tok_len"] is not None]
    ys = [r["k"] for r in ver_all if r["tok_len"] is not None]
    rho = spearman(xs, ys) if len(xs) > 2 else None

    len_stats = {
        "verified_mean_tok": sum(xs) / len(xs) if xs else None,
        "invalid_mean_tok": (sum(r["tok_len"] for r in inv_all if r["tok_len"] is not None)
                             / max(1, sum(1 for r in inv_all if r["tok_len"] is not None))),
        "spearman_toklen_vs_k": rho,
    }

    # ---------- 选择偏差机制：survivor 序号 vs k ----------
    # E2 已证：canonical 评估顺序 ≈ survivor 样本序号降序；first-verified 总选 survivor 最大者。
    # 若 survivor 序号与 k 负相关 → first 规则结构性偏向稀有变体。
    survivor_all = [r["survivor"] for r in ver_all]
    k_all_ver = [r["k"] for r in ver_all]
    rho_sv = spearman(survivor_all, k_all_ver) if len(survivor_all) > 2 else None

    # R0 与 maxk 选中行在其题内 verified 的 survivor 分位（0=最小，1=最大）
    first_pcts, maxk_pcts = [], []
    for p, pp_pick in zip(problems, per_problem):
        ver = [r for r in rows_by_p[p] if r["status"] == "verified_proof"]
        if len(ver) < 2:
            continue
        s_first = next(r["survivor"] for r in ver if r["action"] == pp_pick["picks"]["first"]["action"]
                       and r["sequence"] == pp_pick["picks"]["first"]["seq"])
        s_maxk = next(r["survivor"] for r in ver if r["action"] == pp_pick["picks"]["maxk"]["action"]
                      and r["sequence"] == pp_pick["picks"]["maxk"]["seq"])
        first_pcts.append(sum(1 for r in ver if r["survivor"] < s_first) / (len(ver) - 1))
        maxk_pcts.append(sum(1 for r in ver if r["survivor"] < s_maxk) / (len(ver) - 1))

    bias_stats = {
        "spearman_survivor_vs_k": rho_sv,
        "first_mean_survivor_percentile": sum(first_pcts) / len(first_pcts) if first_pcts else None,
        "maxk_mean_survivor_percentile": sum(maxk_pcts) / len(maxk_pcts) if maxk_pcts else None,
    }

    # ---------- 打印 ----------
    print(f"数据：{len(problems)} 题 / {len(ver_all)} 条 verified / 预算 B={B}")
    print()
    print("== 各选择规则的总体指标（20 题）==")
    print(f"{'规则':>12} {'Σk':>5} {'mean k':>7} {'k=1 题数':>8} {'k≤2 题数':>8} {'平均重发现':>10}")
    label = {"first": "R0 first", "maxk": "R1 maxk", "mintok": "R2 mintok",
             "minchar": "R3 minchar", "RAND(期望)": "RAND(期望)"}
    for ru in rules + ["RAND(期望)"]:
        a = agg[ru]
        nk2 = "—" if a["n_k_le2"] is None else f"{a['n_k_le2']:.2f}" if isinstance(a["n_k_le2"], float) else str(a["n_k_le2"])
        nk1 = round(a["n_k1"], 2) if not isinstance(a["n_k1"], int) else a["n_k1"]
        print(f"{label[ru]:>12} {a['sum_k']:>7.1f} {a['mean_k']:>7.3f} {nk1:>8} "
              f"{nk2:>8} {a['mean_rediscover']:>10.3f}")
    print()
    print(f"规则间动作差异数（共 20 题）：first≠maxk: {diff12}；first≠mintok: {diff13}；"
          f"first≠minchar: {diff14}；maxk≠mintok: {diff23}")
    print()
    print("== token 长度与 k ==")
    print(f"verified 平均 token 数: {len_stats['verified_mean_tok']:.2f}；"
          f"invalid 平均 token 数: {len_stats['invalid_mean_tok']:.2f}")
    print(f"verified 集合内 Spearman(tok_len, k) = {rho:.3f}")
    print()
    print("== 选择偏差机制 ==")
    print(f"verified 集合内 Spearman(survivor, k) = {rho_sv:.3f}（负相关=序号越大越稀有）")
    if bias_stats["first_mean_survivor_percentile"] is not None:
        print(f"R0 选中的 survivor 分位均值 = {bias_stats['first_mean_survivor_percentile']:.2f}；"
              f"R1(maxk) = {bias_stats['maxk_mean_survivor_percentile']:.2f}（0=最小, 1=最大）")
    print()
    print("== 多解题（n_verified ≥ 2）逐题对照 ==")
    print(f"{'problem':>22} {'n':>2} | {'R0 first':<34} | {'R1 maxk':<34} | {'R2 mintok':<34}")
    for pp in per_problem:
        if pp["n_verified"] < 2:
            continue
        def cell(ru):
            pk = pp["picks"][ru]
            return f"k={pk['k']:>2} t={pk['tok_len']} {pk['action'][:24]}"
        print(f"{pp['problem']:>22} {pp['n_verified']:>2} | {cell('first'):<34} | {cell('maxk'):<34} | {cell('mintok'):<34}")
    print()
    print("== R0→R2 选择变化的具体动作 ==")
    changed = 0
    for pp in per_problem:
        a, b = pp["picks"]["first"], pp["picks"]["mintok"]
        if a["action"] != b["action"]:
            changed += 1
            print(f"{pp['problem']}:")
            print(f"   R0: k={a['k']:>2} tok={a['tok_len']:>2}  {a['action'][:64]!r}")
            print(f"   R2: k={b['k']:>2} tok={b['tok_len']:>2}  {b['action'][:64]!r}")
    if changed == 0:
        print("（无变化）")

    out = {
        "budget": B,
        "n_problems": len(problems),
        "n_verified": len(ver_all),
        "rules_aggregate": agg,
        "rule_action_diff_counts": {"first_vs_maxk": diff12, "first_vs_mintok": diff13,
                                    "first_vs_minchar": diff14, "maxk_vs_mintok": diff23},
        "token_len_stats": len_stats,
        "selection_bias": bias_stats,
        "per_problem": per_problem,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print()
    print("saved:", args.out)


if __name__ == "__main__":
    main()

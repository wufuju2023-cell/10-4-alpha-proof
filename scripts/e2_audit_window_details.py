#!/usr/bin/env python3
"""E2 细化分析：失败对照、两种 logprob 口径、selected 明细、transitions.jsonl 交叉核对。"""

from __future__ import annotations

import glob
import json
import math
import os
import statistics

E2 = os.environ.get("E2_WORK", os.path.expanduser("~/e2-work"))
TRAIN = f"{E2}/training/remote/tmp/fate-m-formal-w001-shared-rollout-v2"
JOIN = f"{E2}/join/remote/tmp/fate-m-formal-w001-join-v2/joins"
TRANS = (f"{E2}/training/remote/mnt/workspace/experiments/"
         f"fate_m_20x200_ce_vs_online_v2_7b_20261005/runs/formal-20x10/"
         f"wave_001/ce/update/replay/transitions.jsonl")
OUT = f"{E2}/e2-window-audit-details.json"

B = 64
DELTA = 0.1
THETA = math.log(1.0 / DELTA) / B
LOG10 = math.log(10)


def pct(vals, q):
    xs = sorted(vals)
    return xs[max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))]


def load_all():
    problems = sorted(d for d in os.listdir(TRAIN) if d.startswith("fate_m_"))
    rows = []
    for p in problems:
        req = json.load(open(glob.glob(f"{TRAIN}/{p}/actor_receipts/*/policy_requests/*.json")[0]))
        cands = req["candidates"]
        canon, results, selected = {}, {}, None
        for line in open(f"{TRAIN}/{p}/observer.jsonl"):
            ev = json.loads(line)
            k = ev.get("kind")
            if k == "canonical_candidate":
                canon[ev["candidate_index"]] = ev
            elif k == "canonical_candidate_result":
                results[ev["candidate_index"]] = ev
            elif k == "canonical_selected_path":
                selected = ev
        sel_ids = set(selected.get("selected_event_ids", [])) if selected else set()
        for ci, ev in canon.items():
            res = results.get(ci, {})
            si = ev["survivor_sample_index"]
            rc = cands[si]
            rows.append({
                "problem": p,
                "action": ev["action"],
                "k": len(ev["sample_indices"]),
                "logp_old": sum(rc["raw_completion_old_logprobs"]),
                "logp_samp": sum(rc["raw_completion_sampling_logprobs"]),
                "status": res.get("verifier_status"),
                "selected": ev.get("event_id") in sel_ids,
            })
    return rows


def desc(vals):
    return {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "p10": round(pct(vals, 0.10), 3),
        "p90": round(pct(vals, 0.90), 3),
    }


def main():
    rows = load_all()
    ver = [r for r in rows if r["status"] == "verified_proof"]
    inv = [r for r in rows if r["status"] == "invalid_tactic"]

    # A) 先验分布对照（换算成 -log10）
    out = {"theta_B": round(THETA, 5), "B": B, "delta": DELTA}

    out["verified"] = {
        "empirical_freq": desc([r["k"] / B for r in ver]),
        "minus_log10_p_model": desc([-r["logp_old"] / LOG10 for r in ver]),
        "minus_log10_p_samp": desc([-r["logp_samp"] / LOG10 for r in ver]),
        "in_window_empirical": sum(1 for r in ver if r["k"] / B >= THETA),
        "in_window_p_model": sum(1 for r in ver if math.exp(r["logp_old"]) >= THETA),
        "in_window_p_samp": sum(1 for r in ver if math.exp(r["logp_samp"]) >= THETA),
    }
    out["invalid"] = {
        "empirical_freq": desc([r["k"] / B for r in inv]),
        "minus_log10_p_model": desc([-r["logp_old"] / LOG10 for r in inv]),
        "minus_log10_p_samp": desc([-r["logp_samp"] / LOG10 for r in inv]),
        "in_window_empirical": sum(1 for r in inv if r["k"] / B >= THETA),
        "in_window_p_model": sum(1 for r in inv if math.exp(r["logp_old"]) >= THETA),
        "in_window_p_samp": sum(1 for r in inv if math.exp(r["logp_samp"]) >= THETA),
    }

    print("== A. verified vs invalid ==")
    print("verified n={} in-window(emp/p_model/p_samp) = {}/{}/{}, total {}".format(
        len(ver),
        out["verified"]["in_window_empirical"],
        out["verified"]["in_window_p_model"],
        out["verified"]["in_window_p_samp"],
        len(ver)))
    print("  emp freq:", out["verified"]["empirical_freq"])
    print("  -log10 p_model:", out["verified"]["minus_log10_p_model"])
    print("  -log10 p_samp :", out["verified"]["minus_log10_p_samp"])
    print("invalid n={} in-window(emp/p_model/p_samp) = {}/{}/{}, total {}".format(
        len(inv),
        out["invalid"]["in_window_empirical"],
        out["invalid"]["in_window_p_model"],
        out["invalid"]["in_window_p_samp"],
        len(inv)))
    print("  emp freq:", out["invalid"]["empirical_freq"])
    print("  -log10 p_model:", out["invalid"]["minus_log10_p_model"])
    print("  -log10 p_samp :", out["invalid"]["minus_log10_p_samp"])
    print()

    # B) selected 明细（含 p_samp）
    sel = sorted([r for r in rows if r["selected"]], key=lambda r: r["problem"])
    out["selected"] = [
        {"problem": r["problem"], "action": r["action"], "k": r["k"],
         "p_emp": round(r["k"] / B, 4),
         "minus_log10_p_model": round(-r["logp_old"] / LOG10, 2),
         "minus_log10_p_samp": round(-r["logp_samp"] / LOG10, 2)}
        for r in sel
    ]
    print("== B. selected 20 ==")
    print(f"{'problem':22} {'k':>2} {'emp':>6} {'-lg10 pm':>8} {'-lg10 ps':>8}  action")
    for s in out["selected"]:
        print(f"{s['problem']:22} {s['k']:>2} {s['p_emp']:>6} "
              f"{s['minus_log10_p_model']:>8} {s['minus_log10_p_samp']:>8}  {s['action'][:45]!r}")
    print()

    # C) transitions.jsonl 交叉核对
    t = [json.loads(l) for l in open(TRANS)]
    out["transitions_jsonl"] = {
        "n": len(t),
        "keys": sorted(t[0].keys()) if t else [],
    }
    # token 统计：找 action/action_tokens 类字段
    tok_sum = None
    for f in ("action_tokens", "action_token_ids", "tokens"):
        if t and f in t[0]:
            tok_sum = sum(len(x[f]) for x in t)
            out["transitions_jsonl"]["token_field"] = f
            out["transitions_jsonl"]["token_total"] = tok_sum
    sel_actions = {(s["problem"], s["action"]) for s in out["selected"]}
    t_actions = set()
    for x in t:
        a = x.get("action") or (x.get("transition") or {}).get("action")
        p = x.get("problem_id") or (x.get("transition") or {}).get("problem_id")
        if a is not None and p is not None:
            t_actions.add((p, a))
    out["transitions_jsonl"]["actions_matching_selected"] = len(t_actions & sel_actions)
    out["transitions_jsonl"]["actions_total"] = len(t_actions)
    print("== C. transitions.jsonl ==")
    print(json.dumps(out["transitions_jsonl"], ensure_ascii=False))
    print("sample keys:", out["transitions_jsonl"]["keys"])
    if t:
        print("first entry:", {k: str(v)[:70] for k, v in t[0].items()})
    print()

    # D) fate_m_009 罕见发现检查
    r9 = [r for r in rows if r["problem"] == "fate_m_009_v001" and r["selected"]]
    if r9:
        r = r9[0]
        print("== D. fate_m_009 selected 罕见检查 ==")
        print(f"  action={r['action']!r}")
        print(f"  k={r['k']}  p_model={math.exp(r['logp_old']):.3e}  p_samp={math.exp(r['logp_samp']):.3e}")
        print(f"  expected count under p_model in 64 draws: {64*math.exp(r['logp_old']):.2e}")
    print()

    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
    print("saved:", OUT)


if __name__ == "__main__":
    main()

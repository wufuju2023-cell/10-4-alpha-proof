#!/usr/bin/env python3
"""E2 采样窗审计（audit_window）

输入（解压自 evidence zips）：
  ~/e2-work/training/remote/tmp/fate-m-formal-w001-shared-rollout-v2/<problem>/
      actor_receipts/*/policy_requests/*.json   64 个原始样本（含两种 logprob）
      observer.jsonl                             canonical 去重与执行/验证结果
  ~/e2-work/join/remote/tmp/fate-m-formal-w001-join-v2/joins/<problem>/strict-replay/receipt.json

输出：
  stdout 表格 + ~/e2-work/e2-window-audit.json

口径：
  - k        = 该战术在 64 次采样中的命中次数（canonical sample_indices 长度）
  - p_emp    = k / 64                    （经验采样频率，censor 于 1/64）
  - p_model  = exp(Σ raw_completion_old_logprobs)     （管线内部记录的模型概率）
  - p_samp   = exp(Σ raw_completion_sampling_logprobs)（采样视角记录值，对照）
  - θ_B      = ln(1/δ)/B，B=64，δ=0.1 → ≈ 0.036       （单次预算下的采样窗）
"""

from __future__ import annotations

import collections
import glob
import json
import math
import os
import statistics

E2 = os.environ.get("E2_WORK", os.path.expanduser("~/e2-work"))
TRAIN = f"{E2}/training/remote/tmp/fate-m-formal-w001-shared-rollout-v2"
JOIN = f"{E2}/join/remote/tmp/fate-m-formal-w001-join-v2/joins"
OUT = f"{E2}/e2-window-audit.json"

B = 64           # 每题原始采样次数（预算）
DELTA = 0.1
THETA = math.log(1.0 / DELTA) / B   # ≈ 0.03598


def comb(n, k):
    if k < 0 or k > n:
        return 0.0
    return float(math.comb(n, k))


def p_find_hypergeom(K, Bp, N=64):
    """从 N 次样本中不放回取 Bp 次，至少命中 1 个成功样本的概率。"""
    if K <= 0:
        return 0.0
    if Bp >= N:
        return 1.0
    return 1.0 - comb(N - K, Bp) / comb(N, Bp)


def load_problem(p):
    reqs = glob.glob(f"{TRAIN}/{p}/actor_receipts/*/policy_requests/*.json")
    if len(reqs) != 1:
        raise RuntimeError(f"{p}: expected 1 policy request, got {len(reqs)}")
    req = json.load(open(reqs[0]))
    raw = []
    for i, c in enumerate(req["candidates"]):
        raw.append({
            "i": i,
            "text": c["returned_text"],
            "logp_old": sum(c["raw_completion_old_logprobs"]),
            "logp_samp": sum(c["raw_completion_sampling_logprobs"]),
        })

    canon, results, selected = {}, {}, None
    for line in open(f"{TRAIN}/{p}/observer.jsonl"):
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
        rc = raw[si]
        rows.append({
            "problem": p,
            "ci": ci,
            "action": ev["action"],
            "k": len(ev["sample_indices"]),
            "survivor": si,
            "logp_old": rc["logp_old"],
            "logp_samp": rc["logp_samp"],
            "status": res.get("verifier_status"),
            "did_execute": bool(res.get("did_execute")),
            "selected": ev.get("event_id") in sel_ids,
            "event_id": ev.get("event_id"),
        })
    return rows, selected


def pct(vals, q):
    if not vals:
        return None
    xs = sorted(vals)
    idx = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[idx]


def main():
    problems = sorted(d for d in os.listdir(TRAIN) if d.startswith("fate_m_"))
    all_rows, problem_stats, selected_rows = [], [], []
    sel_receipt_actions = {}

    for p in problems:
        rows, selected = load_problem(p)
        all_rows.extend(rows)

        # 与 join receipt 的 selected_path 对照
        jr = json.load(open(f"{JOIN}/{p}/strict-replay/receipt.json"))
        sel_receipt_actions[p] = [sp["action"] for sp in jr.get("selected_path", [])]

        verified = [r for r in rows if r["status"] == "verified_proof"]
        invalid = [r for r in rows if r["status"] == "invalid_tactic"]
        K_ver = sum(r["k"] for r in verified)
        K_all = sum(r["k"] for r in rows)

        for r in rows:
            if r["selected"]:
                selected_rows.append(r)

        pstat = {
            "problem": p,
            "n_canonical": len(rows),
            "n_verified": len(verified),
            "n_invalid": len(invalid),
            "k_verified_total": K_ver,
            "k_all_total": K_all,
            "verified_k": sorted((r["k"] for r in verified), reverse=True),
            "verified_minus_log10_p_model": [round(-r["logp_old"] / math.log(10), 2) for r in verified],
            "verified_minus_log10_p_emp": [round(-math.log10(max(r["k"] / B, 1 / B)), 2) for r in verified],
            "p_find_curve": {str(bp): round(p_find_hypergeom(K_ver, bp), 4) for bp in (4, 8, 16, 32, 64)},
            "receipt_selected_actions": sel_receipt_actions[p],
        }
        problem_stats.append(pstat)

    # ---------- 总量核对 ----------
    n_raw = 64 * len(problems)
    n_canon = len(all_rows)
    n_exec = sum(1 for r in all_rows if r["did_execute"])
    verified = [r for r in all_rows if r["status"] == "verified_proof"]
    invalid = [r for r in all_rows if r["status"] == "invalid_tactic"]

    # ---------- 校准分析 ----------
    calib_buckets = collections.defaultdict(list)
    for r in all_rows:
        x = -r["logp_old"] / math.log(10)  # -log10(p_model)
        b = ("[0,0.5)" if x < 0.5 else "[0.5,1)" if x < 1 else "[1,2)" if x < 2
             else "[2,4)" if x < 4 else "[4+)")
        calib_buckets[b].append(r["k"] / B)

    calib = {}
    for b in ("[0,0.5)", "[0.5,1)", "[1,2)", "[2,4)", "[4+)"):
        vals = calib_buckets.get(b, [])
        calib[b] = {
            "n": len(vals),
            "mean_freq": round(statistics.fmean(vals), 4) if vals else None,
            "median_freq": round(statistics.median(vals), 4) if vals else None,
        }

    # ---------- 成功战术的分布 ----------
    ver_freq = [r["k"] / B for r in verified]
    ver_mp = [-r["logp_old"] / math.log(10) for r in verified]
    ver_ms = [-r["logp_samp"] / math.log(10) for r in verified]
    in_window = [r for r in verified if r["k"] / B >= THETA]
    borderline = [r for r in verified if r["k"] in (1, 2)]
    lucky1 = [r for r in verified if r["k"] == 1]

    # 选择路径（20 条 transition）
    sel_stats = []
    for r in selected_rows:
        sel_stats.append({
            "problem": r["problem"],
            "action": r["action"],
            "k": r["k"],
            "freq": round(r["k"] / B, 4),
            "minus_log10_p_model": round(-r["logp_old"] / math.log(10), 2),
        })

    # ---------- 聚合 ----------
    agg = {
        "budget": {"B": B, "delta": DELTA, "theta_B": round(THETA, 4)},
        "totals": {
            "problems": len(problems),
            "raw_samples": n_raw,
            "canonical": n_canon,
            "executed": n_exec,
            "verified_proof": len(verified),
            "invalid_tactic": len(invalid),
            "selected_transitions": len(selected_rows),
        },
        "calibration_p_model_vs_freq": calib,
        "verified": {
            "freq": {"mean": round(statistics.fmean(ver_freq), 4),
                     "median": round(statistics.median(ver_freq), 4),
                     "p90": round(pct(ver_freq, 0.9), 4)},
            "minus_log10_p_model": {"min": round(min(ver_mp), 2),
                                    "median": round(statistics.median(ver_mp), 2),
                                    "max": round(max(ver_mp), 2)},
            "minus_log10_p_samp": {"min": round(min(ver_ms), 2),
                                   "median": round(statistics.median(ver_ms), 2),
                                   "max": round(max(ver_ms), 2)},
            "in_window_fraction": round(len(in_window) / len(verified), 4),
            "k_hist": dict(sorted(collections.Counter(r["k"] for r in verified).items())),
        },
        "selected_transitions": sel_stats,
        "problems": problem_stats,
    }
    json.dump(agg, open(OUT, "w"), ensure_ascii=False, indent=2)

    # ---------- 打印 ----------
    print(f"B={B}, δ={DELTA}, θ_B={THETA:.4f}")
    print(f"problems={len(problems)} raw={n_raw} canonical={n_canon} "
          f"executed={n_exec} verified={len(verified)} invalid={len(invalid)}")
    print()
    print("== 校准：-log10(p_model) 桶 vs 经验频率 (k/64) ==")
    for b, v in calib.items():
        print(f"  {b:>8}: n={v['n']:>4} mean_freq={v['mean_freq']} median_freq={v['median_freq']}")
    print()
    print("== 49 个 verified_proof：命中次数 k 直方图 ==")
    print(" ", dict(sorted(collections.Counter(r['k'] for r in verified).items())))
    print(f"  in-window (k/64 ≥ θ_B): {len(in_window)}/{len(verified)} "
          f"({round(100*len(in_window)/len(verified),1)}%)")
    print(f"  borderline (k=1,2): {len(borderline)}")
    print(f"  single-draw (k=1): {len(lucky1)}")
    print("  负 log10 p_model 分位: min={:.2f} med={:.2f} max={:.2f}".format(
        min(ver_mp), statistics.median(ver_mp), max(ver_mp)))
    print()
    print("== 每题发现曲线 P(find | B' 次采样) ==")
    print(f"  {'problem':22} {'K_ver':>5} {'B4':>6} {'B8':>6} {'B16':>6} {'B32':>6} {'B64':>6}")
    for ps in problem_stats:
        c = ps["p_find_curve"]
        print(f"  {ps['problem']:22} {ps['k_verified_total']:>5} "
              f"{c['4']:>6} {c['8']:>6} {c['16']:>6} {c['32']:>6} {c['64']:>6}")
    print()
    print("== 20 条 selected transitions ==")
    for s in sel_stats:
        print(f"  {s['problem']:22} k={s['k']:>2} freq={s['freq']:>6} "
              f"-log10p={s['minus_log10_p_model']:>6}  {s['action'][:50]!r}")
    print()
    print("output:", OUT)


if __name__ == "__main__":
    main()

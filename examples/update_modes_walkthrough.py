#!/usr/bin/env python3
# 【源代码｜F-ex-walkthrough】examples/update_modes_walkthrough.py — 数值走查：价值目标 + 两种 policy 更新
"""数值小例子：价值目标回溯 + 两种 policy 更新（纯标准库，无任何依赖）。

用法:
    python3 examples/update_modes_walkthrough.py

手工数字直接代入，逐步打印中间量：
  A. 价值目标回溯（终端 0 / OR -1+子 / AND min）——看“最坏分支”如何决定；
  B. 离线 CE（模仿搜索选中动作）——损失/梯度/参数更新逐步；
  C. 在线 REINFORCE+KL 锚——Δ、β 的作用与闭式平衡点验证；
  D. 有限差分梯度校验（解析梯度 vs 数值梯度）；
  E. 在线 batch 平均（r=0 的样本对 policy 无梯度）。
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

# ---------------------------------------------------------------------- #
# 工具
# ---------------------------------------------------------------------- #


def softmax(z: Sequence[float]) -> List[float]:
    m = max(z)
    e = [math.exp(zi - m) for zi in z]
    s = sum(e)
    return [ei / s for ei in e]


def log_softmax(z: Sequence[float]) -> List[float]:
    m = max(z)
    lse = m + math.log(sum(math.exp(zi - m) for zi in z))
    return [zi - lse for zi in z]


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append(" | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# A. 价值目标回溯
# ---------------------------------------------------------------------- #


class Node:
    def __init__(self, name: str, to_play: str = "OR", terminal: bool = False) -> None:
        self.name = name
        self.to_play = to_play
        self.terminal = terminal
        self.children: Dict[str, "Node"] = {}
        self.is_optimal = False
        self.value_target: float | None = None

    def add(self, action: str, child: "Node") -> None:
        self.children[action] = child


def compute_value_target(node: Node) -> float:
    if node.terminal:
        node.value_target = 0.0
        return 0.0
    if node.to_play == "OR":
        action, child = next((a, c) for a, c in node.children.items() if c.is_optimal)
        value = -1.0 + compute_value_target(child)
    else:
        value = min(compute_value_target(c) for c in node.children.values())
    node.value_target = value
    return value


def section_a() -> None:
    print("=" * 72)
    print("A. 价值目标回溯：d(s) = -value_target")
    print("   规则：终端 0 ｜ OR = -1 + 最优子 ｜ AND = min(子)")
    print()
    leaf_x = Node("x: proved", terminal=True)
    node_x = Node("x-branch(OR)")
    node_x.add("close-x", leaf_x)
    leaf_y = Node("y: proved", terminal=True)
    node_y2 = Node("y-mid(OR)")
    node_y2.add("close-y", leaf_y)
    node_y = Node("y-branch(OR)")
    node_y.add("progress-y", node_y2)
    and_node = Node("split(AND)", to_play="AND")
    and_node.add("focus 0", node_x)
    and_node.add("focus 1", node_y)
    root = Node("root(OR)")
    root.add("split", and_node)

    for name, node in (("root", root), ("split(AND)", and_node), ("x-branch", node_x),
                       ("y-branch", node_y), ("y-mid", node_y2)):
        node.is_optimal = True
    leaf_x.is_optimal = True
    leaf_y.is_optimal = True

    value = compute_value_target(root)
    rows = []
    for name, node in (("x: proved", leaf_x), ("y: proved", leaf_y), ("y-mid(OR)", node_y2),
                       ("y-branch(OR)", node_y), ("x-branch(OR)", node_x),
                       ("split(AND)", and_node), ("root(OR)", root)):
        rows.append([name, f"d = {abs(float(node.value_target)):.1f}", "terminal" if node.terminal else node.to_play])
    print(table(["节点", "剩余步数", "类型"], rows))
    print()
    print(f"root.value_target = -1 + min(-1, -2) = {value:.1f}   →  d(root) = {-value:.1f} 步")
    print("结论：AND 取 min —— 最坏（最长）分支决定总体剩余步数；")
    print("      y 分支多一层，就把整棵树的价值从 -1 压到 -2。")
    assert value == -3.0
    print()


# ---------------------------------------------------------------------- #
# B. 离线 CE：模仿“搜索选中的动作”
# ---------------------------------------------------------------------- #


def ce_loss(z: Sequence[float], teacher: int) -> float:
    return -log_softmax(z)[teacher]


def ce_grad(z: Sequence[float], teacher: int) -> List[float]:
    p = softmax(z)
    g = p[:]
    g[teacher] -= 1.0          # ∂L/∂z = p - onehot(a*)
    return g


def section_b() -> None:
    print("=" * 72)
    print("B. 离线 CE：teacher（搜索选中动作）= B，初始 p = [0.5, 0.5]")
    print("   L = -log p(B)；∂L/∂z = p - onehot(B)；z ← z - η·grad")
    print()
    z = [0.0, 0.0]
    teacher = 1                # 0=A, 1=B
    eta = 2.0                  # 放大步长便于观察
    rows = []
    for step in range(8):
        p = softmax(z)
        loss = ce_loss(z, teacher)
        grad = ce_grad(z, teacher)
        rows.append([str(step), f"[{p[0]:.4f}, {p[1]:.4f}]", f"{loss:.4f}",
                     f"[{grad[0]:+.4f}, {grad[1]:+.4f}]"])
        z = [zi - eta * gi for zi, gi in zip(z, grad)]
    p = softmax(z)
    print(table(["step", "p = [A, B]", "L", "grad"], rows))
    print()
    print(f"8 步后 p(B) = {p[1]:.4f}，损失 → {ce_loss(z, teacher):.4f}")
    print("结论：CE 是监督信号——梯度方向恒定“把 a*=B 推高”，与奖励无关。")
    assert p[1] > 0.95
    print()


# ---------------------------------------------------------------------- #
# C. 在线 REINFORCE + KL 锚
# ---------------------------------------------------------------------- #


def online_loss(z: Sequence[float], action: int, logp_old: float, r: float,
                beta: float) -> float:
    logp = log_softmax(z)[action]
    delta = logp - logp_old
    return -r * delta + beta * delta * delta


def online_grad(z: Sequence[float], action: int, logp_old: float, r: float,
                beta: float) -> List[float]:
    logp = log_softmax(z)[action]
    delta = logp - logp_old
    p = softmax(z)
    coef = -r + 2.0 * beta * delta          # 有效系数（KL 项的作用）
    e = [0.0, 0.0]
    e[action] = 1.0
    return [coef * (ei - pi) for pi, ei in zip(p, e)]   # coef·(e − p)


def run_online(z0: Sequence[float], action: int, r: float, beta: float,
               eta: float = 0.05, steps: int = 400) -> Tuple[List[float], List[List[float]]]:
    z = list(z0)
    logp_old = log_softmax(z0)[action]
    history = []
    for _ in range(steps):
        p = softmax(z)
        delta = log_softmax(z)[action] - logp_old
        coef = -r + 2.0 * beta * delta
        history.append([p[:], delta, coef])
        grad = online_grad(z, action, logp_old, r, beta)
        z = [zi - eta * gi for zi, gi in zip(z, grad)]
    return z, history


def section_c() -> None:
    print("=" * 72)
    print("C. 在线 REINFORCE + KL 锚：采样动作 = A，r = +1，π_old = [0.5, 0.5]")
    print("   L = -r·Δ + β·Δ²；有效系数 = -r + 2βΔ；平衡点 Δ* = r/(2β)")
    print()
    for beta in (0.05, 5.0):
        z, hist = run_online([0.0, 0.0], action=0, r=1.0, beta=beta)
        p = softmax(z)
        delta_star = 1.0 / (2.0 * beta)
        p_star = 0.5 * math.exp(delta_star)
        print(f"β = {beta:<5}: 400 步后 p(A) = {p[0]:.4f}；"
              f"闭式平衡 p*(A) = 0.5·e^(1/(2β)) = {p_star:.4f}"
              + ("（超出概率范围 → 实际不设限）" if p_star > 1.0 else ""))
        picked = [hist[i] for i in (0, 5, 50, 399)]
        rows = [[str(i), f"{h[0][0]:.4f}", f"{h[1]:+.4f}", f"{h[2]:+.4f}"] for i, h in zip((0, 5, 50, 399), picked)]
        print(table(["step", "p(A)", "Δ", "系数 -r+2βΔ"], rows))
        print()
    print("结论：β=0.05 时锚几乎不设限（p(A) 趋近 1）；")
    print("      β=5 时锚把 p(A) 稳在闭式平衡 0.5526 附近——KL 项确实在起“软步长”作用。")
    z_final, _ = run_online([0.0, 0.0], action=0, r=1.0, beta=5.0)
    p_final = softmax(z_final)[0]
    assert abs(p_final - 0.5 * math.exp(0.1)) < 0.01
    print()


# ---------------------------------------------------------------------- #
# D. 有限差分梯度校验
# ---------------------------------------------------------------------- #


def numeric_grad(f, z: Sequence[float], eps: float = 1e-6) -> List[float]:
    out = []
    for i in range(len(z)):
        zp, zm = list(z), list(z)
        zp[i] += eps
        zm[i] -= eps
        out.append((f(zp) - f(zm)) / (2 * eps))
    return out


def max_abs_diff(a: Sequence[float], b: Sequence[float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def section_d() -> None:
    print("=" * 72)
    print("D. 有限差分校验：解析梯度 vs 数值梯度（ε=1e-6）")
    print()
    z = [0.3, -0.2]
    ana = ce_grad(z, 1)
    num = numeric_grad(lambda zz: ce_loss(zz, 1), z)
    print(f"CE      : 解析 {['%+.6f' % g for g in ana]}  数值 {['%+.6f' % g for g in num]}")
    assert max_abs_diff(ana, num) < 1e-6

    ana = online_grad(z, 0, logp_old=math.log(0.5), r=1.0, beta=0.05)
    num = numeric_grad(lambda zz: online_loss(zz, 0, math.log(0.5), 1.0, 0.05), z)
    print(f"在线    : 解析 {['%+.6f' % g for g in ana]}  数值 {['%+.6f' % g for g in num]}")
    assert max_abs_diff(ana, num) < 1e-6
    print("\n结论：两个损失的解析梯度都通过了数值校验——公式与实现一致。")
    print()


# ---------------------------------------------------------------------- #
# E. 在线 batch 平均
# ---------------------------------------------------------------------- #


def section_e() -> None:
    print("=" * 72)
    print("E. batch 平均：3 条样本的梯度叠加（同一条轨迹共享 r 时会高度相关）")
    print("   取采样时策略 π_old = [0.5, 0.5]（即 logp_old = log 0.5）")
    print()
    z = [0.0, 0.0]
    logp_old = math.log(0.5)
    samples = [(0, +1.0, "A 成功"), (1, -1.0, "B 失败"), (0, 0.0, "A 未闭合 r=0")]
    grads = []
    rows = []
    for action, r, tag in samples:
        g = online_grad(z, action, logp_old, r, 0.05)
        grads.append(g)
        rows.append([tag, f"r={r:+.1f}", f"[{g[0]:+.4f}, {g[1]:+.4f}]"])
    mean = [sum(g[i] for g in grads) / len(grads) for i in range(2)]
    rows.append(["batch 平均", "-", f"[{mean[0]:+.4f}, {mean[1]:+.4f}]"])
    print(table(["样本", "奖励", "policy 梯度  coef·(e−p)"], rows))
    print()
    print("此时 z 恰好等于采样时的 z（Δ=0），所以：")
    print("  r=+1 → 把 A 推高；r=−1 → 把 B 压低；r=0 → policy 梯度为 0。")
    print("注意：只有 2 个动作时，“推高 A”与“压低 B”是同一个方向")
    print("      （概率和恒为 1，只有 1 维自由度）；动作更多时正/负奖励才出现不同分量。")
    assert abs(grads[2][0]) < 1e-12 and abs(grads[2][1]) < 1e-12

    # 若模型已偏离采样策略（Δ≠0），r=0 的样本仍有“KL 锚”梯度
    logp_old_shifted = logp_old + 0.2       # 假装当前策略比采样时更看好 A
    g0 = online_grad(z, 0, logp_old_shifted, 0.0, 0.05)
    delta = log_softmax(z)[0] - logp_old_shifted
    expected = [2 * 0.05 * delta * (1.0 - 0.5), 2 * 0.05 * delta * (0.0 - 0.5)]
    print()
    print(f"Δ≠0 时（r=0）：梯度 = {['%+.4f' % g for g in g0]} = 2βΔ·(e−p) = "
          f"{['%+.4f' % g for g in expected]}")
    print("→ 没有奖励信号时，只剩 KL 锚把 log-prob 往旧策略拉。")
    assert max_abs_diff(g0, expected) < 1e-12
    print()
    print("结论：batch 平均就是对单样本梯度的算术平均；")
    print("      这就是 learn_batch_size 的意义——batch=1 只是这个平均值的一个估计。")
    print()


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    print("全部小节通过 ✓")

"""全局配置：默认值一律取“官方对齐”口径。

来源：AlphaProof 补充材料 Table 2/3/6/7、pseudocode.py，以及 N33 对照总表。
本文件中以 `official` 标注的常量均可在运行时覆盖（dataclass 传参或 CLI）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SearchConfig:
    """MCTS 搜索超参（对齐官方 Table 3 + 伪代码）。"""

    # --- 官方 Table 3 ---
    num_samples: int = 6                  # K：每次扩展采样的候选战术数
    c_base: float = 3200.0                # PUCT 探索常数 c_base
    c_init: float = 0.001                 # PUCT 探索常数 c_init
    visit_discount: float = 0.99          # γ：Q = γ^(-1-V)
    prior_temperature: float = 200.0      # τ：先验 exp(logp/τ)（默认已从 50 切到官方 200，可改）
    ps_c: float = 0.01                    # 渐进采样 C
    ps_alpha: float = 0.6                 # 渐进采样 α
    c_and: float = 64.0                   # c_AND：AND 节点探索项乘子（官方 Table 3）
    unvisited_penalty: float = 32.0       # 未访问子节点价值惩罚（官方 Table 3，单位=步）
    no_legal_actions_value: float = -40.0  # 无合法动作/网络失败兜底（官方伪代码 -40）

    # --- 预算（本机实现参数；官方为自适应预算 B(p)，M1 后再对齐）---
    max_nodes: int = 64
    max_steps: int = 64

    # --- E5 试验旋钮（默认 "first" 保持历史/官方行为）---
    # first | most_visited | shortest_action
    optimal_child_policy: str = "first"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NetConfig:
    """策略/价值网络形状（本机 = REAL-Prover 7B decoder-only）。"""

    hidden_size: int = 3584    # 实测值；4096 为历史文档漂移
    value_hidden: int = 256
    value_bins: int = 64       # 官方 Table 2：价值=64 桶；本仓库只保留 64 桶（无 tanh 标量版）


@dataclass(frozen=True)
class TrainConfig:
    """训练/更新超参（两套更新共用）。"""

    value_bins: int = 64
    value_coef: float = 1e-3      # 官方 Table 6：value loss weight = 1e-3
    policy_lr: float = 1e-4       # 本机实现值（官方真值未公开）
    value_lr: float = 3e-4
    beta_kl: float = 0.05         # 在线模式 KL 锚
    max_grad_norm: float = 1.0
    # --- 在线模式：批大小语义 ---
    # learn_batch_size=1 时“一条已验证轨迹更新一次”；
    # learn_batch_size=N 时凑满 N 条（问题或轨迹口径由上层决定）后更新一次。
    learn_batch_size: int = 1
    micro_batch_size: int = 16    # 单次 backward 的最大条数（梯度累积切块）
    # --- 离线模式：回放/混比 ---
    sft_mix: float = 0.1          # 官方 Table 6：SFT 10% 混比
    include_disproof: bool = True  # 官方：反证轨迹进训练（证明+反证双向）
    include_timeout: bool = False  # 官方：超时轨迹不进训练
    disproof_weight: float = 1.0   # 反证样本损失权重（可调）


def default_search_config() -> SearchConfig:
    return SearchConfig()


def default_train_config() -> TrainConfig:
    return TrainConfig()

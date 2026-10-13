"""gpu-serve：GPU 侧参数化 CLI（base / LoRA / value head 全部可插拔）。

子命令（console script：``gpu-serve``，或 ``python -m gpu_serve``）::

    gpu-serve serve-eval   /eval 契约 HTTP 服务（docs/new-python-version-mcts-plan/02 §5）
    gpu-serve train offline 调用 update_offline.OfflineLearner（CE 算法冻结，只注入参数）
    gpu-serve train online  调用 update_online.OnlineLearner（参数化壳，事件回放；live 流待主线）
    gpu-serve eval heldout  在留出池上评测 policy CE / value CE / d_hat 误差
    gpu-serve pool pull|push|merge-logprobs|stats
                            训练池：HF/本地拉取校验、public 上传、logprob 回填、统计
    gpu-serve versions      工具链与组件版本清单
    gpu-serve smoke         真实/ tiny 冒烟

边界：本包只做「装载 + CLI」层；不修改 ``alphaproof/mcts/*`` 与 ``update_*`` 的
更新算法主体（见 docs/architecture/core-and-branches.md 冻结清单）。
"""

from __future__ import annotations

from . import config as config  # noqa: F401  便于 `from gpu_serve import config`

__version__ = "0.1.0"
__all__ = ["__version__", "config"]

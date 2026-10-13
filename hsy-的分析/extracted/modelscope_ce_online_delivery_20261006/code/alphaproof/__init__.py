"""10-4-alpha-proof：AlphaProof 语义对齐版核心包。

对齐口径见仓库根 README 与 docs/alignment.md（以 N33 对照表为准）。
"""

from .config import NetConfig, SearchConfig, TrainConfig

__all__ = ["SearchConfig", "NetConfig", "TrainConfig"]
__version__ = "0.1.0"

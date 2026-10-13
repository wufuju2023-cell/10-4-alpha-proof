"""gpu-serve 配置装载：dataclass 默认值 + 可选 TOML + CLI 覆盖。

优先级：CLI 显式值 > TOML 段 > 代码默认值（dataclass）。

TOML 结构（文件名任意，``gpu-serve --config ./serve.toml ...`` 或
``gpu-serve smoke --config ./serve.toml ...``；所有段/键均可选）::

    [model]
    base = "/mnt/workspace/models/REAL-Prover-fe76f68d"
    lora = "none"                  # 或 adapter 目录 / state_dict 文件
    head = "/mnt/workspace/new_value_head/heads-79efd240/train205628-full-v3/value-head.pt"
    dtype = "auto"                 # auto|bf16|fp16|fp32
    device = "auto"                # auto|cuda|cpu|cuda:0
    hidden_size = 0                # 0 = 从 base config 推断
    value_mid = 256
    value_bins = 64

    [lora]                         # 仅当 --lora 指向裸 state_dict 时使用
    rank = 16
    alpha = 32
    dropout = 0.02
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj"]

    [train]
    steps = 1
    batch_size = 64
    micro_batch_size = 16
    lr = 1e-4                      # policy lr
    value_lr = 3e-4
    value_coef = 1e-3
    beta_kl = 0.05
    sft_mix = 0.1
    disproof_weight = 1.0
    include_disproof = true
    include_timeout = false
    max_grad_norm = 1.0
    seed = 0

    [serve]
    host = "127.0.0.1"
    port = 18080
    token_file = ""
    max_new_tokens = 64
    stop_at_newline = true
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:  # Python >= 3.11
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None  # type: ignore[assignment]

DEFAULT_LORA_TARGET_MODULES: tuple[str, ...] = (
    "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
)

_NONE_LITERALS = {"", "none", "null", "no", "false", "-"}


# --------------------------------------------------------------------------- #
# TOML 读取与优先级工具
# --------------------------------------------------------------------------- #
def load_toml(path: str | os.PathLike[str]) -> dict:
    """读取 TOML 配置；文件不存在直接报错（避免静默用错配置）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config 文件不存在: {p}")
    if tomllib is None:  # pragma: no cover - Python 3.10
        raise RuntimeError("读取 TOML 需要 Python >= 3.11（tomllib）")
    with p.open("rb") as fh:
        return tomllib.load(fh)


def get_section(cfg: Optional[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    if not cfg:
        return {}
    section = cfg.get(name, {}) or {}
    if not isinstance(section, Mapping):
        raise ValueError(f"TOML [{name}] 必须是表（table）")
    return section


def pick(cli_value: Any, cfg: Mapping[str, Any], key: str, default: Any) -> Any:
    """CLI > TOML > 默认。CLI 值为 None 视为「未显式给出」。"""
    if cli_value is not None:
        return cli_value
    if key in cfg and cfg[key] is not None:
        return cfg[key]
    return default


def normalize_optional_path(value: Any) -> str:
    """把 none/null/空串统一为空字符串（= 不启用该组件/文件）。"""
    text = str(value if value is not None else "").strip()
    if text.lower() in _NONE_LITERALS:
        return ""
    return text


def _target_modules(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_LORA_TARGET_MODULES
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value]
    else:
        parts = [p.strip() for p in str(value).split(",")]
    parts = [p for p in parts if p]
    return tuple(parts) if parts else DEFAULT_LORA_TARGET_MODULES


# --------------------------------------------------------------------------- #
# 组件（base / LoRA / value head / 设备与精度）
# --------------------------------------------------------------------------- #
@dataclass
class ModelSetup:
    """组件选择：全部来自参数，换组件不改代码（core-and-branches §3）。"""

    base: str = ""
    lora: str = ""
    head: str = ""
    tokenizer: str = ""
    dtype: str = "auto"
    device: str = "auto"
    hidden_size: int = 0          # 0 = 从 base config / 模型属性推断
    value_mid: int = 256
    value_bins: int = 64
    allow_download: bool = False  # 默认 local_files_only=True（可复现）
    trust_remote_code: bool = False
    revision: str = ""
    strict_head: bool = False     # 头键不匹配时是否直接失败
    tiny: bool = False            # 用 alphaproof.tiny 小模型（无需 HF 权重）


@dataclass
class LoraSpec:
    """裸 LoRA state_dict 的构造参数（目录式 adapter 从 adapter_config.json 自动读取）。"""

    rank: int = 16
    alpha: int = 32
    dropout: float = 0.02
    target_modules: tuple[str, ...] = DEFAULT_LORA_TARGET_MODULES


@dataclass
class TrainHyper:
    """训练/更新超参（映射到 alphaproof.config.TrainConfig，算法主体不动）。"""

    steps: int = 1
    batch_size: int = 64          # offline：每个 optimizer step 的样本数；online：learn_batch_size
    micro_batch_size: int = 16
    lr: float = 1e-4              # policy_lr
    value_lr: float = 3e-4
    value_coef: float = 1e-3
    beta_kl: float = 0.05
    sft_mix: float = 0.1
    disproof_weight: float = 1.0
    include_disproof: bool = True
    include_timeout: bool = False
    max_grad_norm: float = 1.0
    seed: int = 0

    def to_train_config(self, learn_batch_size: Optional[int] = None):
        """构造 alphaproof.config.TrainConfig（懒惰导入，避免无 torch 时也可打印配置）。"""
        from alphaproof.config import TrainConfig

        return TrainConfig(
            policy_lr=float(self.lr),
            value_lr=float(self.value_lr),
            value_coef=float(self.value_coef),
            beta_kl=float(self.beta_kl),
            sft_mix=float(self.sft_mix),
            disproof_weight=float(self.disproof_weight),
            include_disproof=bool(self.include_disproof),
            include_timeout=bool(self.include_timeout),
            max_grad_norm=float(self.max_grad_norm),
            micro_batch_size=int(self.micro_batch_size),
            learn_batch_size=int(self.batch_size if learn_batch_size is None else learn_batch_size),
        )

    def to_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


# --------------------------------------------------------------------------- #
# 从 CLI namespace + TOML 构造
# --------------------------------------------------------------------------- #
def build_model_setup(ns: Any, cfg: Optional[Mapping[str, Any]]) -> ModelSetup:
    sec = get_section(cfg, "model")
    return ModelSetup(
        base=str(pick(getattr(ns, "base", None), sec, "base", "") or ""),
        lora=normalize_optional_path(pick(getattr(ns, "lora", None), sec, "lora", "")),
        head=normalize_optional_path(pick(getattr(ns, "head", None), sec, "head", "")),
        tokenizer=str(pick(getattr(ns, "tokenizer", None), sec, "tokenizer", "") or ""),
        dtype=str(pick(getattr(ns, "dtype", None), sec, "dtype", "auto") or "auto"),
        device=str(pick(getattr(ns, "device", None), sec, "device", "auto") or "auto"),
        hidden_size=int(pick(getattr(ns, "hidden_size", None), sec, "hidden_size", 0) or 0),
        value_mid=int(pick(getattr(ns, "value_mid", None), sec, "value_mid", 256)),
        value_bins=int(pick(getattr(ns, "value_bins", None), sec, "value_bins", 64)),
        allow_download=bool(pick(getattr(ns, "allow_download", None), sec, "allow_download", False)),
        trust_remote_code=bool(pick(getattr(ns, "trust_remote_code", None), sec,
                                    "trust_remote_code", False)),
        revision=str(pick(getattr(ns, "revision", None), sec, "revision", "") or ""),
        strict_head=bool(pick(getattr(ns, "strict_head", None), sec, "strict_head", False)),
        tiny=bool(pick(getattr(ns, "tiny", None), sec, "tiny", False)),
    )


def build_lora_spec(ns: Any, cfg: Optional[Mapping[str, Any]]) -> LoraSpec:
    sec = get_section(cfg, "lora")
    rank = pick(getattr(ns, "rank", None), sec, "rank", 0)
    if rank in (0, "0", None):
        # 兼容 [model] 段里的旧键
        rank = pick(None, get_section(cfg, "model"), "rank", 16)
    alpha = pick(getattr(ns, "lora_alpha", None), sec, "alpha", None)
    if alpha is None:
        alpha = pick(None, get_section(cfg, "model"), "lora_alpha", 32)
    dropout = pick(getattr(ns, "lora_dropout", None), sec, "dropout", None)
    if dropout is None:
        dropout = pick(None, get_section(cfg, "model"), "lora_dropout", 0.02)
    targets = pick(getattr(ns, "lora_target_modules", None), sec, "target_modules", None)
    if targets is None:
        targets = pick(None, get_section(cfg, "model"), "lora_target_modules", None)
    return LoraSpec(rank=int(rank or 16), alpha=int(alpha or 32), dropout=float(dropout or 0.0),
                    target_modules=_target_modules(targets))


def build_train_hyper(ns: Any, cfg: Optional[Mapping[str, Any]]) -> TrainHyper:
    sec = get_section(cfg, "train")

    def g(key: str, default: Any) -> Any:
        return pick(getattr(ns, key, None), sec, key, default)

    return TrainHyper(
        steps=int(g("steps", 1)),
        batch_size=int(g("batch_size", 64)),
        micro_batch_size=int(g("micro_batch_size", 16)),
        lr=float(g("lr", 1e-4)),
        value_lr=float(g("value_lr", 3e-4)),
        value_coef=float(g("value_coef", 1e-3)),
        beta_kl=float(g("beta_kl", 0.05)),
        sft_mix=float(g("sft_mix", 0.1)),
        disproof_weight=float(g("disproof_weight", 1.0)),
        include_disproof=bool(g("include_disproof", True)),
        include_timeout=bool(g("include_timeout", False)),
        max_grad_norm=float(g("max_grad_norm", 1.0)),
        seed=int(g("seed", 0)),
    )


def build_serve_options(ns: Any, cfg: Optional[Mapping[str, Any]]) -> dict:
    sec = get_section(cfg, "serve")
    return {
        "host": str(pick(getattr(ns, "host", None), sec, "host", "127.0.0.1")),
        "port": int(pick(getattr(ns, "port", None), sec, "port", 18080)),
        "token_file": str(pick(getattr(ns, "token_file", None), sec, "token_file", "") or ""),
        "engine": str(pick(getattr(ns, "engine", None), sec, "engine", "auto") or "auto"),
        "max_new_tokens": int(pick(getattr(ns, "max_new_tokens", None), sec, "max_new_tokens", 64)),
        "stop_at_newline": bool(pick(getattr(ns, "stop_at_newline", None), sec,
                                     "stop_at_newline", True)),
        "model_revision": str(pick(getattr(ns, "model_revision", None), sec,
                                   "model_revision", "") or ""),
        "adapter": str(pick(getattr(ns, "adapter", None), sec, "adapter", "") or ""),
    }

"""组件装载层：base / LoRA / value head / tokenizer / 设备与精度。

设计约束（docs/architecture/core-and-branches.md §3）：
- 一切组件由参数注入，换 base（如 Qwen3.8-27B）只改 flag/TOML；
- value head 的 hidden size 自适应：显式 ``--hidden-size`` > base config > 模型属性；
- 重依赖（torch / transformers / peft）全部延迟导入，``--help``、mock 引擎、
  ``versions`` 等路径在无 torch 环境下也能运行。

本模块只做「装载」，不触碰任何训练/更新算法。
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import LoraSpec, ModelSetup

LOG = logging.getLogger("gpu_serve.components")

_DTYPE_KWARG: Optional[str] = None


# --------------------------------------------------------------------------- #
# 设备 / 精度
# --------------------------------------------------------------------------- #
def resolve_device(name: str) -> str:
    import torch

    value = (name or "auto").strip()
    if value in ("", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    if value.startswith("cuda") and not torch.cuda.is_available():
        raise SystemExit(f"--device {value} 不可用：torch.cuda.is_available()=False")
    return value


def resolve_dtype(name: str, device: str):
    import torch

    value = (name or "auto").strip().lower()
    if value in ("", "auto"):
        return torch.bfloat16 if device.startswith("cuda") else torch.float32
    table = {
        "bf16": torch.bfloat16, "bfloat16": torch.bfloat16,
        "fp16": torch.float16, "float16": torch.float16, "half": torch.float16,
        "fp32": torch.float32, "float32": torch.float32, "float": torch.float32,
    }
    if value not in table:
        raise SystemExit(f"未知 --dtype {name!r}（可选 auto|bf16|fp16|fp32）")
    return table[value]


# --------------------------------------------------------------------------- #
# base / tokenizer / LoRA
# --------------------------------------------------------------------------- #
def _is_peft_adapter_dir(path: str | Path) -> bool:
    return (Path(path) / "adapter_config.json").is_file()


def _dtype_kwarg_name() -> str:
    """transformers 4.x 用 torch_dtype，5.x 推荐 dtype；按签名自动选择。"""
    global _DTYPE_KWARG
    if _DTYPE_KWARG is None:
        from transformers import AutoModelForCausalLM

        try:
            params = inspect.signature(AutoModelForCausalLM.from_pretrained).parameters
        except (TypeError, ValueError):  # pragma: no cover
            params = {}
        if "dtype" in params:
            _DTYPE_KWARG = "dtype"
        elif "torch_dtype" in params:
            _DTYPE_KWARG = "torch_dtype"
        else:
            _DTYPE_KWARG = "dtype"  # transformers>=5 经 **kwargs 透传
    return _DTYPE_KWARG


def _from_pretrained_with_dtype(cls, base: str, dtype, kwargs: dict):
    """兼容 dtype/torch_dtype 两种关键字（含 **kwargs 透传的版本）。"""
    kw = _dtype_kwarg_name()
    try:
        return cls.from_pretrained(base, **{kw: dtype}, **kwargs)
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword" not in message and "unexpected argument" not in message:
            raise
        alt = "torch_dtype" if kw == "dtype" else "dtype"
        return cls.from_pretrained(base, **{alt: dtype}, **kwargs)


def load_tokenizer(setup: ModelSetup):
    """tokenizer 默认随 base；padding 缺失时退化为 eos（serve/eval 需要）。"""
    from transformers import AutoTokenizer

    path = setup.tokenizer or setup.base
    kwargs = {
        "local_files_only": not setup.allow_download,
        "trust_remote_code": setup.trust_remote_code,
    }
    if setup.revision:
        kwargs["revision"] = setup.revision
    LOG.info("loading tokenizer %s (local_files_only=%s)", path, kwargs["local_files_only"])
    tokenizer = AutoTokenizer.from_pretrained(path, **kwargs)
    if getattr(tokenizer, "pad_token_id", None) is None and \
            getattr(tokenizer, "eos_token_id", None) is not None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def attach_lora(model, lora_path: str, spec: LoraSpec, trainable: bool = False):
    """挂载 LoRA：目录式 PEFT adapter 优先；裸 state_dict 按 LoraSpec 构造后装载。

    ``trainable=True`` 时确保 adapter 参数 requires_grad（训练命令必须传）：
    PEFT 的 ``PeftModel.from_pretrained`` 默认 is_trainable=False（推理冻结），
    直接训练会得到空的 policy 参数组。
    """
    from peft import LoraConfig, PeftModel, get_peft_model

    path = Path(lora_path)
    if path.is_dir() and (path / "adapter_config.json").is_file():
        if trainable:
            try:
                peft_model = PeftModel.from_pretrained(model, str(path), is_trainable=True)
            except (TypeError, ValueError):
                peft_model = PeftModel.from_pretrained(model, str(path))
                for name, param in peft_model.named_parameters():
                    if "lora_" in name:
                        param.requires_grad_(True)
        else:
            peft_model = PeftModel.from_pretrained(model, str(path))
        report = {
            "mode": "peft_dir",
            "path": str(path),
            "trainable_params": int(sum(p.numel() for p in peft_model.parameters()
                                         if p.requires_grad)),
        }
        return peft_model, report

    import torch

    obj = torch.load(str(path), map_location="cpu")
    state = obj
    if isinstance(obj, dict):
        for key in ("state_dict", "model", "adapter"):
            inner = obj.get(key)
            if isinstance(inner, dict) and inner:
                state = inner
                break
    rank = int(spec.rank)
    for key, value in state.items():
        if "lora_A" in key and hasattr(value, "shape") and len(value.shape) == 2:
            rank = int(value.shape[0])
            break
    lora_config = LoraConfig(
        r=rank, lora_alpha=int(spec.alpha), lora_dropout=float(spec.dropout),
        target_modules=list(spec.target_modules), bias="none", task_type="CAUSAL_LM",
    )
    peft_model = get_peft_model(model, lora_config)
    missing, unexpected = peft_model.load_state_dict(state, strict=False)
    report = {
        "mode": "state_dict",
        "path": str(path),
        "rank": rank,
        "alpha": int(spec.alpha),
        "target_modules": list(spec.target_modules),
        "missing": len(missing),
        "unexpected": len(unexpected),
    }
    return peft_model, report


def load_backbone(setup: ModelSetup, lora_spec: Optional[LoraSpec] = None,
                  trainable_lora: bool = False):
    """装载 base（支持 --base 直接指向 PEFT adapter 目录）+ 可选 --lora。"""
    from transformers import AutoModelForCausalLM

    base = setup.base
    base_is_adapter = bool(base) and _is_peft_adapter_dir(base)
    lora = setup.lora
    info: dict[str, Any] = {"base": base, "base_is_adapter": base_is_adapter}

    if base_is_adapter:
        adapter_cfg = json.loads((Path(base) / "adapter_config.json").read_text(encoding="utf-8"))
        real_base = adapter_cfg.get("base_model_name_or_path")
        if not real_base:
            raise SystemExit(f"adapter {base} 的 adapter_config.json 缺 base_model_name_or_path")
        info["adapter_base"] = real_base
        base = real_base
        if not lora:
            lora = setup.base  # 挂载 --base 指定目录中的 adapter

    device = resolve_device(setup.device)
    dtype = resolve_dtype(setup.dtype, device)
    kwargs = {
        "local_files_only": not setup.allow_download,
        "trust_remote_code": setup.trust_remote_code,
    }
    if setup.revision and not base_is_adapter:
        kwargs["revision"] = setup.revision
    dtype_kw = _dtype_kwarg_name()
    LOG.info("loading base %s (dtype_kw=%s, dtype=%s, device=%s)", base, dtype_kw, dtype, device)
    model = _from_pretrained_with_dtype(AutoModelForCausalLM, base, dtype, kwargs)
    model = model.to(device)
    model.eval()
    info["dtype"] = str(dtype).replace("torch.", "")
    info["device"] = device

    lora_report = None
    if lora:
        model, lora_report = attach_lora(model, lora, lora_spec or LoraSpec(),
                                         trainable=trainable_lora)
        model = model.to(device)
        model.eval()
    info["lora"] = lora_report
    return model, info


# --------------------------------------------------------------------------- #
# value head
# --------------------------------------------------------------------------- #
def infer_hidden_size(model, override: int = 0) -> int:
    """hidden size 自适应：显式覆盖 > config.hidden_size / text_config.hidden_size > 模型属性。"""
    if override:
        return int(override)
    cfg = getattr(model, "config", None)
    for obj in (cfg, getattr(cfg, "text_config", None)):
        if obj is None:
            continue
        for attr in ("hidden_size", "n_embd", "d_model", "decoder_hidden_size"):
            value = getattr(obj, attr, None)
            if isinstance(value, int) and value > 0:
                return value
    hidden = getattr(model, "hidden_size", None)  # TinyCausalLM
    if isinstance(hidden, int) and hidden > 0:
        return hidden
    raise SystemExit("无法从 base config 推断 hidden_size；请显式传 --hidden-size")


def build_value_head(setup: ModelSetup, model, device: str):
    """构造 64 桶价值头并加载权重（load_s18_head 兼容裸/包装/无前缀键名）。"""
    from alphaproof.net.value_head import ValueHead64, load_s18_head

    hidden = infer_hidden_size(model, setup.hidden_size)
    head = ValueHead64(hidden_size=hidden, mid=int(setup.value_mid), bins=int(setup.value_bins))
    report: dict[str, Any] = {
        "hidden_size": hidden, "mid": int(setup.value_mid), "bins": int(setup.value_bins),
        "path": setup.head,
    }
    if setup.head:
        loaded = load_s18_head(head, setup.head)
        report["missing"] = loaded["missing"]
        report["unexpected"] = loaded["unexpected"]
        if (loaded["missing"] or loaded["unexpected"]) and setup.strict_head:
            raise SystemExit(
                "价值头与 base 不匹配："
                f"missing={loaded['missing']} unexpected={loaded['unexpected']}；"
                "检查 --hidden-size / --value-mid / --value-bins，或去掉 --strict-head"
            )
    head = head.to(device)
    head.eval()
    return head, report


# --------------------------------------------------------------------------- #
# 打包装载
# --------------------------------------------------------------------------- #
@dataclass
class LoadedComponents:
    model: Any
    tokenizer: Any
    head: Any
    device: str
    hidden_size: int
    model_revision: str = ""
    adapter: Optional[str] = None
    info: dict = field(default_factory=dict)
    head_report: dict = field(default_factory=dict)
    encode_fn: Any = None
    tiny: bool = False


def _revision_name(base: str) -> str:
    text = str(base or "").rstrip("/")
    return Path(text).name if text else ""


def load_components(setup: ModelSetup, require_head: bool = True,
                    lora_spec: Optional[LoraSpec] = None,
                    trainable_lora: bool = False) -> LoadedComponents:
    """按 ModelSetup 装载完整组件（tiny 或真实 base）。

    ``trainable_lora=True`` 供训练命令使用（确保 LoRA 参数可训练）。
    """
    if require_head and not setup.head and not setup.tiny:
        raise SystemExit("需要 --head <value-head.pt>（64 桶价值头权重）")

    if setup.tiny:
        from alphaproof.net.value_head import ValueHead64
        from alphaproof.tiny import build_tiny, tiny_encode_fn

        model, tokenizer = build_tiny()
        device = resolve_device(setup.device)
        model = model.to(device)
        model.eval()
        head = ValueHead64(hidden_size=model.hidden_size, mid=int(setup.value_mid),
                           bins=int(setup.value_bins)).to(device)
        head.eval()
        hidden = int(model.hidden_size)
        return LoadedComponents(
            model=model, tokenizer=tokenizer, head=head, device=device, hidden_size=hidden,
            model_revision=setup.revision or "tiny", adapter=None,
            info={"tiny": True, "device": device, "dtype": "float32"},
            head_report={"hidden_size": hidden, "path": "(tiny fresh head)"},
            encode_fn=tiny_encode_fn(tokenizer, device=device), tiny=True,
        )

    if not setup.base:
        raise SystemExit("需要 --base <模型路径/HF id>（或用 --tiny）")

    from update_offline.learner import make_hf_encode_fn

    model, info = load_backbone(setup, lora_spec, trainable_lora=trainable_lora)
    tokenizer = load_tokenizer(setup)
    device = info.get("device") or resolve_device(setup.device)
    head = None
    head_report: dict[str, Any] = {}
    if setup.head or require_head:
        head, head_report = build_value_head(setup, model, device)
    adapter = Path(setup.lora).name if setup.lora else (
        Path(setup.base).name if info.get("base_is_adapter") else None)
    return LoadedComponents(
        model=model, tokenizer=tokenizer, head=head, device=device,
        hidden_size=int(head_report.get("hidden_size")
                        or infer_hidden_size(model, setup.hidden_size)),
        model_revision=setup.revision or _revision_name(info.get("base") or setup.base),
        adapter=adapter, info=info, head_report=head_report,
        encode_fn=make_hf_encode_fn(tokenizer, device=device), tiny=False,
    )

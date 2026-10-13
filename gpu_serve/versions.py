"""``versions``：工具链与组件版本清单（不加载模型权重，最快路径）。"""

from __future__ import annotations

import importlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .config import get_section


def _module_version(name: str) -> Optional[str]:
    try:
        mod = importlib.import_module(name)
        return str(getattr(mod, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 - 缺依赖不是错误
        return None


def _git_revision(repo_root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def describe_base(base: str) -> dict:
    """读取本地 HF 目录的 config.json 摘要（不加载权重、不需要 torch）。"""
    info: dict[str, Any] = {"base": base}
    p = Path(base)
    if not p.exists():
        info["note"] = "不是本地路径（HF id 或路径不存在），未检查"
        return info
    if p.is_dir() and (p / "adapter_config.json").is_file():
        try:
            adapter = json.loads((p / "adapter_config.json").read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"adapter_config.json 读取失败: {exc!r}"
            return info
        info["peft_adapter"] = {
            key: adapter.get(key) for key in
            ("base_model_name_or_path", "r", "lora_alpha", "lora_dropout", "target_modules")
            if key in adapter
        }
        return info
    cfg_path = p / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"config.json 读取失败: {exc!r}"
            return info
        text_cfg = cfg.get("text_config") or {}
        info["config"] = {
            "model_type": cfg.get("model_type"),
            "architectures": cfg.get("architectures"),
            "hidden_size": cfg.get("hidden_size", text_cfg.get("hidden_size")),
            "vocab_size": cfg.get("vocab_size", text_cfg.get("vocab_size")),
            "torch_dtype": cfg.get("torch_dtype") or cfg.get("dtype"),
        }
    else:
        info["note"] = "目录缺 config.json"
    return info


def describe_head(path: str) -> dict:
    """value head 权重摘要：文件大小 + 键名形状（有 torch 时）。"""
    info: dict[str, Any] = {"head": path}
    p = Path(path)
    if not p.is_file():
        info["note"] = "文件不存在"
        return info
    info["bytes"] = p.stat().st_size
    try:
        import torch
    except Exception:  # noqa: BLE001
        info["note"] = "无 torch，仅文件大小"
        return info
    try:
        obj = torch.load(str(p), map_location="cpu")
        state = obj
        if isinstance(obj, dict):
            for wrapper in ("state_dict", "model", "value_head"):
                inner = obj.get(wrapper)
                if isinstance(inner, dict) and inner and hasattr(next(iter(inner.values())), "shape"):
                    state = inner
                    break
        if isinstance(state, dict):
            keys = list(state.keys())
            info["num_tensors"] = len(keys)
            info["shapes"] = {key: list(state[key].shape) for key in keys
                              if hasattr(state[key], "shape")}
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"torch.load 失败: {exc!r}"
    return info


def run_versions(args, cfg) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    model_sec = get_section(cfg, "model")
    base = getattr(args, "base", None) or model_sec.get("base") or ""
    head = getattr(args, "head", None) or model_sec.get("head") or ""
    lora = getattr(args, "lora", None) or model_sec.get("lora") or ""

    info: dict[str, Any] = {
        "gpu_serve": __version__,
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "torch": _module_version("torch"),
        "transformers": _module_version("transformers"),
        "peft": _module_version("peft"),
        "safetensors": _module_version("safetensors"),
        "repo_root": str(repo_root),
        "git_revision": _git_revision(repo_root),
    }
    if base:
        info["base_info"] = describe_base(str(base))
    if lora and str(lora).lower() not in ("none", "null", ""):
        info["lora_info"] = describe_base(str(lora))
    if head:
        info["head_info"] = describe_head(str(head))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0

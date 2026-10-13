"""推理引擎：``/eval`` 契约的服务端实现。

三种引擎，接口一致（``eval(prompt, k, temperature) -> dict``）：

- ``HFEvalEngine``：真实 HF 因果 LM + value head（云端用）；
- ``TinyEvalEngine``：alphaproof.tiny 小模型（CPU 测试 / smoke）；
- ``MockEvalEngine``：无任何重依赖的确定性引擎（契约联调 / 无 torch 测试）。

logprob 语义：返回模型原始策略下的序列 log 概率（sum of log softmax），
temperature 只影响采样；CPU 端 MCTS 的 prior 温度由它自己的 prior_temperature 处理。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from .components import LoadedComponents
from .config import ModelSetup

LOG = logging.getLogger("gpu_serve.engines")


class BaseEvalEngine:
    model_revision: str = "unknown"
    adapter: Optional[str] = None

    def eval(self, prompt: str, k: int, temperature: float) -> dict:
        raise NotImplementedError

    def health(self) -> dict:
        return {"status": "ok", "model_revision": self.model_revision, "adapter": self.adapter}

    def _response(self, candidates: list[dict], d_hat: float) -> dict:
        return {
            "candidates": candidates,
            "value": {"v": -float(d_hat), "d_hat": float(d_hat)},
            "model_revision": self.model_revision,
            "adapter": self.adapter,
        }


# --------------------------------------------------------------------------- #
# Mock：无 torch / transformers，仅用于契约联调与无依赖测试
# --------------------------------------------------------------------------- #
class MockEvalEngine(BaseEvalEngine):
    """确定性假引擎：不加载任何模型，不代表模型质量。"""

    def __init__(self, model_revision: str = "mock", adapter: Optional[str] = None) -> None:
        self.model_revision = model_revision or "mock"
        self.adapter = adapter

    def eval(self, prompt: str, k: int, temperature: float) -> dict:
        digest = hashlib.sha256(str(prompt).encode("utf-8")).digest()
        d_hat = 1.0 + (int.from_bytes(digest[:4], "big") % 6400) / 100.0
        candidates = []
        for i in range(int(k)):
            h = hashlib.sha256(digest + i.to_bytes(2, "little")).digest()
            candidates.append({
                "tactic": "mock_" + h.hex()[:8],
                "logprob": round(-0.5 - (int.from_bytes(h[:2], "big") % 500) / 100.0, 4),
            })
        return self._response(candidates, d_hat)

    def health(self) -> dict:
        return {"status": "ok", "engine": "mock", "model_revision": self.model_revision,
                "adapter": self.adapter}


# --------------------------------------------------------------------------- #
# Tiny：alphaproof.tiny 小模型（CPU 即可）
# --------------------------------------------------------------------------- #
class TinyEvalEngine(BaseEvalEngine):
    """基于 TinyCausalLM 的采样引擎；自带逐步采样循环（不依赖 HF generate）。"""

    def __init__(self, loaded: LoadedComponents, max_new_tokens: int = 8,
                 stop_at_newline: bool = True, model_revision: str = "tiny",
                 adapter: Optional[str] = None) -> None:
        self.model = loaded.model
        self.tokenizer = loaded.tokenizer
        self.head = loaded.head
        self.device = loaded.device
        self.max_new_tokens = max(1, int(max_new_tokens))
        self.stop_at_newline = bool(stop_at_newline)
        self.model_revision = model_revision or "tiny"
        self.adapter = adapter

    @staticmethod
    def _decode(ids) -> str:
        return "".join(chr(int(i) % 0x110000) for i in ids)

    def eval(self, prompt: str, k: int, temperature: float) -> dict:
        import torch

        token_ids = list(self.tokenizer.encode(prompt))
        input_ids = torch.tensor([token_ids], dtype=torch.long, device=self.device)
        with torch.no_grad():
            out = self.model(input_ids=input_ids, output_hidden_states=True,
                             use_cache=False, return_dict=True)
            hidden = out.hidden_states[-1][:, -1, :].float()
            d_hat = float(self.head.decode(self.head(hidden))[0])

            candidates = []
            for _ in range(int(k)):
                seq = list(token_ids)
                logprob = 0.0
                for _step in range(self.max_new_tokens):
                    x = torch.tensor([seq], dtype=torch.long, device=self.device)
                    step_out = self.model(input_ids=x, use_cache=False, return_dict=True)
                    logits = step_out.logits[0, -1, :].float()
                    logp = torch.log_softmax(logits, dim=-1)
                    if temperature and float(temperature) > 0:
                        probs = torch.softmax(logits / float(temperature), dim=-1)
                        token = int(torch.multinomial(probs, 1).item())
                    else:
                        token = int(torch.argmax(logits).item())
                    logprob += float(logp[token])
                    seq.append(token)
                    if token == 0:  # tiny 词表 0 号当 eos 用
                        break
                text = self._decode(seq[len(token_ids):])
                if self.stop_at_newline:
                    text = text.split("\n")[0]
                candidates.append({"tactic": text.strip(), "logprob": logprob})
        return self._response(candidates, d_hat)


# --------------------------------------------------------------------------- #
# HF：真实模型（云端）
# --------------------------------------------------------------------------- #
class HFEvalEngine(BaseEvalEngine):
    """真实 HF 因果 LM + 64 桶价值头；候选 = 采样生成的 tactic 续写。"""

    def __init__(self, loaded: LoadedComponents, max_new_tokens: int = 64,
                 stop_at_newline: bool = True, model_revision: str = "",
                 adapter: Optional[str] = None) -> None:
        self.model = loaded.model
        self.tokenizer = loaded.tokenizer
        self.head = loaded.head
        self.device = loaded.device
        self.max_new_tokens = max(1, int(max_new_tokens))
        self.stop_at_newline = bool(stop_at_newline)
        self.model_revision = model_revision or loaded.model_revision or "unknown"
        self.adapter = adapter if adapter is not None else loaded.adapter
        self.pad_token_id = getattr(self.tokenizer, "pad_token_id", None)
        self.eos_token_id = getattr(self.tokenizer, "eos_token_id", None)

    # ---------------------------------------------------------------- #
    def eval(self, prompt: str, k: int, temperature: float) -> dict:
        import torch

        enc = self.tokenizer(prompt, return_tensors="pt")
        enc = {key: value.to(self.device) for key, value in enc.items()}
        with torch.no_grad():
            out = self.model(**enc, output_hidden_states=True, use_cache=False,
                             return_dict=True)
            hidden = out.hidden_states[-1][:, -1, :].float()
            d_hat = float(self.head.decode(self.head(hidden))[0])

            sequences, scores = self._generate(enc, int(k), temperature)
            prompt_len = int(enc["input_ids"].shape[1])
            logprobs = self._sequence_logprobs(scores, sequences, prompt_len)
            candidates = self._decode_candidates(sequences, logprobs, prompt_len)
        return self._response(candidates, d_hat)

    # ---------------------------------------------------------------- #
    def _generate(self, enc: dict, k: int, temperature: float):
        import torch

        kwargs: dict[str, Any] = {
            "max_new_tokens": self.max_new_tokens,
            "output_scores": True,
            "return_dict_in_generate": True,
        }
        if self.pad_token_id is not None:
            kwargs["pad_token_id"] = self.pad_token_id
        if self.eos_token_id is not None:
            kwargs["eos_token_id"] = self.eos_token_id
        if temperature and float(temperature) > 0:
            kwargs.update(do_sample=True, temperature=float(temperature),
                          num_return_sequences=int(k))
        else:
            kwargs.update(do_sample=False)
        with torch.no_grad():
            gen = self.model.generate(**enc, **kwargs)
        sequences = gen.sequences
        if sequences.size(0) < k:  # greedy：单序列复制 k 份（logprob 相同）
            idx = torch.zeros(k, dtype=torch.long, device=sequences.device)
            sequences = sequences.index_select(0, idx)
        return sequences, gen.scores

    def _sequence_logprobs(self, scores, sequences, prompt_len: int):
        import torch

        rows = sequences.size(0)
        logprobs = torch.zeros(rows, dtype=torch.float32, device=sequences.device)
        done = torch.zeros(rows, dtype=torch.bool, device=sequences.device)
        for step, step_scores in enumerate(scores or ()):
            position = prompt_len + step
            if position >= sequences.size(1):
                break
            token = sequences[:, position]
            logp = torch.log_softmax(step_scores.float(), dim=-1)
            step_lp = logp.gather(1, token.unsqueeze(1)).squeeze(1)
            logprobs = logprobs + step_lp * (~done)
            if self.eos_token_id is not None:
                done = done | (token == self.eos_token_id)
        return logprobs

    def _decode_candidates(self, sequences, logprobs, prompt_len: int) -> list[dict]:
        candidates = []
        generated = sequences[:, prompt_len:]
        for row in range(generated.size(0)):
            ids = [int(t) for t in generated[row].tolist()]
            if self.eos_token_id is not None and self.eos_token_id in ids:
                ids = ids[: ids.index(self.eos_token_id)]
            text = self.tokenizer.decode(ids, skip_special_tokens=True)
            if self.stop_at_newline:
                text = text.split("\n")[0]
            candidates.append({"tactic": text.strip(), "logprob": float(logprobs[row])})
        return candidates


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
def build_engine(setup: ModelSetup, engine: str = "auto", *, max_new_tokens: int = 64,
                 stop_at_newline: bool = True, model_revision: str = "",
                 adapter: Optional[str] = None, lora_spec=None) -> BaseEvalEngine:
    """按名称构造引擎。auto：tiny 模式 → tiny；给了 base → hf；否则报错。"""
    from .components import load_components

    name = (engine or "auto").strip().lower()
    if name == "auto":
        if setup.tiny:
            name = "tiny"
        elif setup.base:
            name = "hf"
        else:
            raise SystemExit("serve-eval 需要 --base <模型> 或 --engine tiny|mock")
    if name == "mock":
        return MockEvalEngine(model_revision=model_revision or "mock",
                              adapter=adapter or None)
    if name == "tiny":
        loaded = load_components(setup, require_head=True, lora_spec=lora_spec)
        engine_obj = TinyEvalEngine(loaded, max_new_tokens=max_new_tokens,
                                    stop_at_newline=stop_at_newline,
                                    model_revision=model_revision or loaded.model_revision,
                                    adapter=adapter)
        return engine_obj
    if name == "hf":
        loaded = load_components(setup, require_head=True, lora_spec=lora_spec)
        return HFEvalEngine(loaded, max_new_tokens=max_new_tokens,
                            stop_at_newline=stop_at_newline,
                            model_revision=model_revision or loaded.model_revision,
                            adapter=adapter)
    raise SystemExit(f"未知 engine {engine!r}（可选 auto|hf|tiny|mock）")

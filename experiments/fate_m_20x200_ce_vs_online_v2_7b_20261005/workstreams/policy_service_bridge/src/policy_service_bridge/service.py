"""OpenAI-shaped Reap policy endpoint in the live REAL-Prover process.

The service owns no second model.  Callers pass the exact live model/tokenizer
objects used by training, plus an identity callback for the active PEFT
session.  Model calls are serialized because PEFT adapter activation is
process-global; HTTP/session receipt handling remains concurrent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager, nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from .receipts import (canonical_bytes, canonical_sha256, derive_request_seed,
                       payload_sha256, publish_immutable, text_sha256)

try:
    from shared_actor_bridge import (BehaviorIdentity, GenerationParameters,
                                     generate_raw_candidates, generation_receipt_payload,
                                     canonical_sha256, trainable_state_sha256)
except ImportError as exc:  # pragma: no cover - deployment path error
    raise RuntimeError("shared_actor_bridge must be on PYTHONPATH") from exc


SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
ROUTE_RE = re.compile(r"^/sessions/([A-Za-z0-9_-]{1,96})/policy/v1/chat/completions$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IdentitySnapshot:
    policy_version: str
    behavior_version: str
    behavior_sha256: str
    base_version: str
    base_sha256: str
    tokenizer_version: str
    tokenizer_sha256: str

    def validate(self) -> None:
        if not all(isinstance(getattr(self, field), str) and getattr(self, field)
                   for field in ("policy_version", "behavior_version", "base_version", "tokenizer_version")):
            raise ValueError("identity versions must be non-empty strings")
        if self.policy_version != self.behavior_version:
            raise ValueError("active policy and behavior versions differ")
        if any(not SHA_RE.fullmatch(value) for value in (self.behavior_sha256, self.base_sha256, self.tokenizer_sha256)):
            raise ValueError("identity hashes must be lowercase SHA-256")


def _prompt_from_request(raw: Any, generation: GenerationParameters,
                         served_model_id: str) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, dict):
        raise ValueError("request JSON must be an object")
    allowed = {"model", "messages", "n", "temperature", "top_p", "max_tokens", "logprobs", "stream"}
    if set(raw) - allowed:
        raise ValueError("unknown policy request fields")
    messages = raw.get("messages")
    if (not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict)
            or set(messages[0]) != {"role", "content"} or messages[0].get("role") != "user"
            or not isinstance(messages[0].get("content"), str) or not messages[0]["content"]):
        raise ValueError("Reap policy request must contain exactly one non-empty user message")
    model = raw.get("model")
    if model != served_model_id:
        raise ValueError("model differs from pinned served model identity")
    expected = {
        "n": generation.num_return_sequences,
        "temperature": generation.temperature,
        "top_p": generation.top_p,
        "max_tokens": generation.max_new_tokens,
        "logprobs": True,
        "stream": False,
    }
    for key, value in expected.items():
        actual = raw.get(key, value if key in {"top_p", "stream"} else None)
        if isinstance(value, float):
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isclose(float(actual), value, abs_tol=0, rel_tol=0):
                raise ValueError(f"{key} differs from frozen generation contract")
        elif actual != value:
            raise ValueError(f"{key} differs from frozen generation contract")
    normalized = {"model": model, "messages": messages, **expected}
    return normalized, messages[0]["content"]


def _compositional_token_texts(tokenizer: Any, token_ids: list[int],
                               eos_token_id: int | None) -> tuple[str, list[str]]:
    """Render token evidence whose pre-EOS concatenation equals response text.

    Decoding one token at a time is not compositional for byte-level BPE: a
    multi-byte UTF-8 character split across tokens commonly becomes two
    replacement characters.  Reap derives the executed tactic's token span
    from these strings, so that representation can reject an otherwise valid
    whole-sequence decode.  Decode prefixes against the known final text and
    emit text only once a prefix is stable.  This keeps one entry/log-prob per
    raw token while preserving exact token boundaries.  EOS remains explicit
    and outside the action span, matching the existing OpenAI-shaped contract.
    """
    if not token_ids:
        raise ValueError("cannot render an empty token sequence")
    text = tokenizer.decode(token_ids, skip_special_tokens=True)
    if not isinstance(text, str):
        raise TypeError("tokenizer.decode must return text")
    pieces: list[str] = []
    emitted = ""
    last_content_index = len(token_ids)
    if eos_token_id is not None and token_ids[-1] == eos_token_id:
        last_content_index -= 1
    for end in range(1, last_content_index + 1):
        prefix = tokenizer.decode(token_ids[:end], skip_special_tokens=True)
        if (isinstance(prefix, str) and text.startswith(prefix)
                and prefix.startswith(emitted)):
            pieces.append(prefix[len(emitted):])
            emitted = prefix
        else:
            # The prefix ends inside a byte sequence or normalization unit.
            # Its eventual text is emitted by the first later stable boundary.
            pieces.append("")
    if emitted != text:
        if not text.startswith(emitted) or not pieces:
            raise RuntimeError("tokenizer prefix decode cannot reconstruct response text")
        pieces[-1] += text[len(emitted):]
        emitted = text
    if last_content_index != len(token_ids):
        eos_text = tokenizer.decode([token_ids[-1]], skip_special_tokens=False)
        if not isinstance(eos_text, str) or not eos_text:
            raise RuntimeError("EOS token has no explicit text representation")
        pieces.append(eos_text)
    if len(pieces) != len(token_ids) or "".join(pieces[:last_content_index]) != text:
        raise RuntimeError("compositional token rendering invariant failed")
    return text, pieces


class InProcessPolicyService:
    """Generate, rescore and commit a receipt before releasing model output."""

    def __init__(self, *, model: Any, tokenizer: Any, receipt_root: Path,
                 identity_provider: Callable[[str], IdentitySnapshot],
                 activate_session: Callable[[str], None],
                 active_session_provider: Callable[[], str],
                 model_transaction_lock: Any,
                 generation: GenerationParameters,
                 seed_namespace: str, served_model_id: str, actor_config_sha256: str,
                 tokenizer_lock_sha256: str,
                 prompt_transform: Callable[[str, str], str] | None = None,
                 adapter_name_for_session: Callable[[str], str] | None = None,
                 generator: Callable[..., tuple[Any, ...]] = generate_raw_candidates,
                 heartbeat: Callable[[Mapping[str, Any]], None] | None = None,
                 rescore_micro_batch: int = 8, heartbeat_interval_seconds: float = 20.0):
        generation.validate()
        if (not seed_namespace or not served_model_id or not SHA_RE.fullmatch(tokenizer_lock_sha256)
                or not SHA_RE.fullmatch(actor_config_sha256)):
            raise ValueError("seed namespace, served model id, actor config and tokenizer locks are required")
        self.model, self.tokenizer = model, tokenizer
        self.receipt_root = receipt_root.resolve()
        self.identity_provider = identity_provider
        self.activate_session = activate_session
        self.active_session_provider = active_session_provider
        if not hasattr(model_transaction_lock, "__enter__") or not hasattr(model_transaction_lock, "__exit__"):
            raise ValueError("model_transaction_lock must be a shared context-manager lock")
        self.model_transaction_lock = model_transaction_lock
        self.generation = generation
        self.seed_namespace = seed_namespace
        self.served_model_id = served_model_id
        self.actor_config_sha256 = actor_config_sha256
        self.tokenizer_lock_sha256 = tokenizer_lock_sha256
        if prompt_transform is not None and not callable(prompt_transform):
            raise ValueError("prompt_transform must be callable")
        self.prompt_transform = prompt_transform
        self.adapter_name_for_session = adapter_name_for_session or (lambda session_id: session_id)
        if not callable(self.adapter_name_for_session):
            raise ValueError("adapter_name_for_session must be callable")
        self.generator = generator
        self.rescore_micro_batch = rescore_micro_batch
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.heartbeat = heartbeat or (lambda event: print(json.dumps(dict(event), sort_keys=True), flush=True))
        self._sequence_lock = threading.Lock()
        self._next_by_session: dict[str, int] = {}

    @contextmanager
    def _generation_heartbeat(self, session_id: str, request_id: str, started: float):
        stop = threading.Event()

        def pulse() -> None:
            while not stop.wait(self.heartbeat_interval_seconds):
                self.heartbeat({"event": "policy_request_heartbeat", "session_id": session_id,
                                "request_id": request_id,
                                "elapsed_seconds": time.perf_counter() - started,
                                "stage": "generate_and_unwarped_rescore"})

        thread = threading.Thread(target=pulse, name=f"policy-heartbeat-{session_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=min(self.heartbeat_interval_seconds, 1.0))

    def _sequence(self, session_id: str) -> int:
        with self._sequence_lock:
            if session_id not in self._next_by_session:
                directory = self.receipt_root / session_id / "policy_requests"
                seen = []
                if directory.is_dir():
                    for path in directory.glob("*.json"):
                        try:
                            seen.append(int(path.name.split("-", 1)[0]))
                        except ValueError:
                            continue
                self._next_by_session[session_id] = max(seen, default=0) + 1
            sequence = self._next_by_session[session_id]
            self._next_by_session[session_id] += 1
            return sequence

    def handle(self, *, session_id: str, raw_body: bytes) -> tuple[bytes, Path]:
        if not SESSION_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        if not raw_body or len(raw_body) > 8 * 1024 * 1024:
            raise ValueError("request body is empty or exceeds 8 MiB")
        parsed = json.loads(raw_body)
        normalized, incoming_prompt = _prompt_from_request(parsed, self.generation, self.served_model_id)
        request_sha = canonical_sha256(normalized)
        actor_prompt = (incoming_prompt if self.prompt_transform is None
                        else self.prompt_transform(session_id, incoming_prompt))
        if (type(actor_prompt) is not str or not actor_prompt.strip()
                or len(actor_prompt.encode("utf-8")) > 8 * 1024 * 1024):
            raise ValueError("prompt transform must return one non-empty bounded string")
        incoming_prompt_sha = text_sha256(incoming_prompt)
        actor_prompt_sha = text_sha256(actor_prompt)
        sequence = self._sequence(session_id)
        request_id = f"policy-{session_id}-{sequence:08d}-{uuid.uuid4().hex}"
        seed = derive_request_seed(
            seed_namespace=self.seed_namespace, session_id=session_id, sequence=sequence,
            normalized_request_sha256=request_sha, actor_prompt_sha256=actor_prompt_sha)
        started = time.perf_counter()
        self.heartbeat({"event": "policy_request_started", "session_id": session_id,
                        "sequence": sequence, "request_id": request_id})
        # Adapter activation and identity checks share the same lock as generate
        # and rescore, preventing another session from swapping the active PEFT
        # adapter in the middle of a request.
        with self._generation_heartbeat(session_id, request_id, started):
            # The exact same lock object must also wrap optimizer/backward/
            # checkpoint mutations in the learner. A service-private mutex is
            # insufficient for a shared model process.
            with self.model_transaction_lock:
                self.activate_session(session_id)
                expected_adapter = self.adapter_name_for_session(session_id)
                if (not isinstance(expected_adapter, str) or not expected_adapter
                        or self.active_session_provider() != expected_adapter):
                    raise RuntimeError("active PEFT adapter does not match the session's behavior alias")
                before = self.identity_provider(session_id)
                before.validate()
                expected_identity = BehaviorIdentity(
                    policy_version=before.policy_version, behavior_version=before.behavior_version,
                    behavior_sha256=before.behavior_sha256, base_version=before.base_version,
                    base_sha256=before.base_sha256, tokenizer_version=before.tokenizer_version,
                    tokenizer_sha256=before.tokenizer_sha256)
                if before.tokenizer_sha256 != self.tokenizer_lock_sha256:
                    raise RuntimeError("live tokenizer identity differs from pinned tokenizer lock")
                results = self.generator(
                    model=self.model, tokenizer=self.tokenizer, prompt=actor_prompt,
                    generation=self.generation, request_id=request_id, request_seed=seed,
                    expected_identity=expected_identity,
                    # The surrounding service scope already owns this exact
                    # shared lock across activation, identity, generation and
                    # post-generation identity. The actor helper otherwise
                    # reacquires it, which would deadlock a non-reentrant lock.
                    model_transaction=nullcontext,
                    activate_behavior=lambda: self.activate_session(session_id),
                    live_identity=lambda: BehaviorIdentity(**asdict(self.identity_provider(session_id))),
                    rescore_micro_batch=self.rescore_micro_batch,
                )
                after = self.identity_provider(session_id)
                after.validate()
                if self.active_session_provider() != expected_adapter:
                    raise RuntimeError("active PEFT behavior adapter changed during policy request")
                if after != before:
                    raise RuntimeError("behavior/base identity changed during policy request")
        if len(results) != self.generation.num_return_sequences:
            raise RuntimeError("generator returned wrong candidate count")
        prompt_ids = list(results[0].prompt_token_ids)
        if not prompt_ids or any(list(item.prompt_token_ids) != prompt_ids for item in results):
            raise RuntimeError("candidate prompt token IDs differ")
        candidates, choices = [], []
        completion_tokens = 0
        eos = getattr(self.tokenizer, "eos_token_id", None)
        generation_sha256 = self.generation.sha256
        identity = asdict(before)
        behavior_identity = BehaviorIdentity(**identity)
        for index, item in enumerate(results):
            if (item.request_id != request_id or item.candidate_index != index or item.request_seed != seed):
                raise RuntimeError("candidate order or seed drift")
            raw_ids = list(item.raw_completion_token_ids)
            old = [float(value) for value in item.raw_completion_old_logprobs]
            sampling = [float(value) for value in item.raw_completion_sampling_logprobs]
            if (not raw_ids or len(old) != len(raw_ids) or len(sampling) != len(raw_ids)
                    or item.finish_reason not in {"stop", "length"}
                    or not SHA_RE.fullmatch(item.service_candidate_sha256)):
                raise RuntimeError("raw sample identity or token/log-prob evidence is incomplete")
            eos_positions = [position for position, token in enumerate(raw_ids) if token == eos]
            if ((item.finish_reason == "stop" and eos_positions != [len(raw_ids) - 1])
                    or (item.finish_reason == "length" and
                        (eos_positions or len(raw_ids) != self.generation.max_new_tokens))):
                raise RuntimeError("raw sample finish reason does not match EOS/token limit")
            if any(not math.isfinite(x) for x in old + sampling):
                raise RuntimeError("raw sample log-probs must be finite")
            identity_sha256 = canonical_sha256(asdict(behavior_identity))
            sample_identity = {
                "request_id": request_id, "candidate_index": index,
                "request_seed": seed, "prompt_token_ids": prompt_ids,
                "raw_completion_token_ids": raw_ids,
                "raw_completion_old_logprobs": old,
                "raw_completion_sampling_logprobs": sampling,
                "finish_reason": item.finish_reason,
                "generation_params_sha256": generation_sha256,
                "behavior_identity_sha256": identity_sha256,
            }
            if item.service_candidate_sha256 != canonical_sha256(sample_identity):
                raise RuntimeError("raw sample identity hash does not match captured generation")
            text, token_texts = _compositional_token_texts(self.tokenizer, raw_ids, eos)
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("raw completion decodes to an empty Reap tactic response")
            token_entries = []
            for token_id, logprob, token_text in zip(raw_ids, old, token_texts, strict=True):
                token_entries.append({"token": token_text, "bytes": list(token_text.encode("utf-8")),
                                      "token_id": token_id, "logprob": logprob})
            finish_reason = item.finish_reason
            candidate = {
                "request_id": request_id, "candidate_index": index, "returned_text": text,
                "raw_completion_token_ids": raw_ids,
                "raw_completion_old_logprobs": old,
                "raw_completion_sampling_logprobs": sampling,
                "request_seed": seed, "generation_params_sha256": generation_sha256,
                "finish_reason": finish_reason,
                "wall_seconds": float(item.wall_seconds),
                "gpu_seconds": float(item.gpu_seconds),
                "service_candidate_sha256": item.service_candidate_sha256,
            }
            candidates.append(candidate)
            choices.append({"index": index,
                            "raw_sample_index": index,
                            "service_candidate_sha256": item.service_candidate_sha256,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": finish_reason,
                            "logprobs": {"content": token_entries}})
            completion_tokens += len(raw_ids)
        response = {
            "id": request_id, "object": "chat.completion", "created": int(time.time()),
            "model": normalized["model"], "choices": choices,
            "usage": {"prompt_tokens": len(prompt_ids), "completion_tokens": completion_tokens,
                      "total_tokens": len(prompt_ids) + completion_tokens},
        }
        response_body = canonical_bytes(response)
        generation_receipt = generation_receipt_payload(
            request_id=request_id, request_seed=seed, prompt_token_ids=prompt_ids,
            generation=self.generation, identity=behavior_identity, raw_samples=candidates)
        receipt: dict[str, Any] = {
            "schema_version": "fate.policy_request.v2", "status": "committed",
            "request_id": request_id, "session_id": session_id, "request_sequence": sequence,
            "route": f"/sessions/{session_id}/policy/v1/chat/completions",
            "raw_request_sha256": hashlib.sha256(raw_body).hexdigest(),
            "seed_namespace": self.seed_namespace,
            "served_model_id": self.served_model_id,
            "actor_config_sha256": self.actor_config_sha256,
            "tokenizer_lock_sha256": self.tokenizer_lock_sha256,
            "normalized_request": normalized,
            "incoming_prompt": incoming_prompt,
            "actor_prompt": actor_prompt,
            "prompt_binding": {
                "normalized_request_sha256": request_sha,
                "incoming_prompt_sha256": incoming_prompt_sha,
                "actor_prompt_sha256": actor_prompt_sha,
                "transform_applied": self.prompt_transform is not None,
            },
            "generation_contract": {**asdict(self.generation),
                                    "generation_params_sha256": generation_sha256},
            "request_seed": seed, "prompt": actor_prompt,
            "prompt_token_ids": prompt_ids, "actor_prompt_token_ids": prompt_ids,
            "behavior_identity": identity, "candidates": candidates,
            "generation_receipt": generation_receipt,
            "generation_receipt_sha256": canonical_sha256(generation_receipt),
            "openai_response": response,
            "request_cost": {"prompt_tokens": len(prompt_ids), "generated_tokens": completion_tokens,
                             "wall_seconds": sum(item["wall_seconds"] for item in candidates),
                             "gpu_seconds": sum(item["gpu_seconds"] for item in candidates)},
            "proxy_binding": {
                "normalized_request_sha256": request_sha,
                "response_body_sha256": hashlib.sha256(response_body).hexdigest(),
            "behavior_identity_sha256": canonical_sha256(identity),
            },
        }
        receipt["receipt_sha256"] = payload_sha256(receipt)
        relative = Path(session_id) / "policy_requests" / f"{sequence:08d}-{request_id}.json"
        path = publish_immutable(self.receipt_root, relative, receipt)
        self.heartbeat({"event": "policy_request_committed", "session_id": session_id,
                        "sequence": sequence, "request_id": request_id,
                        "receipt_sha256": receipt["receipt_sha256"],
                        "candidates": len(candidates), "elapsed_seconds": time.perf_counter() - started})
        return response_body, path


class _Handler(BaseHTTPRequestHandler):
    server: "PolicyHTTPServer"

    def log_message(self, fmt: str, *args: object) -> None:
        self.server.service.heartbeat({"event": "policy_http", "message": fmt % args})

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = canonical_bytes({"ok": True, "service": "fate-policy-service-v2"})
            self._send(200, body)
        else:
            self._send(404, b'{"error":"not_found"}')

    def do_POST(self) -> None:  # noqa: N802
        match = ROUTE_RE.fullmatch(self.path.split("?", 1)[0])
        if match is None:
            self._send(404, b'{"error":"not_found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8 * 1024 * 1024:
                raise ValueError("invalid Content-Length")
            body, _ = self.server.service.handle(session_id=match.group(1), raw_body=self.rfile.read(length))
            self._send(200, body)
        except (ValueError, json.JSONDecodeError) as exc:
            self.server.service.heartbeat({"event": "policy_request_rejected", "error_type": type(exc).__name__})
            self._send(400, canonical_bytes({"error": "invalid_policy_request", "type": type(exc).__name__}))
        except BaseException as exc:
            # Model output is never returned when identity checking or immutable
            # receipt publication fails.
            self.server.service.heartbeat({"event": "policy_request_failed", "error_type": type(exc).__name__})
            self._send(503, canonical_bytes({"error": "policy_request_failed", "type": type(exc).__name__}))


class PolicyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: InProcessPolicyService):
        self.service = service
        super().__init__(address, _Handler)


def start_policy_server(service: InProcessPolicyService, *, host: str = "127.0.0.1", port: int = 0) -> tuple[PolicyHTTPServer, threading.Thread]:
    server = PolicyHTTPServer((host, port), service)
    thread = threading.Thread(target=server.serve_forever, name="fate-policy-http", daemon=True)
    thread.start()
    service.heartbeat({"event": "policy_service_ready", "host": host,
                       "port": server.server_address[1], "generation": asdict(service.generation)})
    return server, thread


def make_live_identity_provider(*, model: Any, policy_version: Callable[[str], str],
                                base_version: str, base_sha256: str,
                                tokenizer_version: str, tokenizer_sha256: str,
                                max_trainable_bytes: int = 512 * 1024 * 1024) -> Callable[[str], IdentitySnapshot]:
    """Build an identity callback over the same live PEFT model object.

    The caller's ``activate_session`` runs before this callback.  Consequently
    the hash covers the active trainable adapter, not a serialized sidecar or a
    model loaded in a second process.  ``base_sha256`` must be the precomputed
    pinned base-artifact identity; hashing all 7B weights per request is both
    wasteful and outside the rollout transaction.
    """
    if (not base_version or not tokenizer_version or not SHA_RE.fullmatch(base_sha256)
            or not SHA_RE.fullmatch(tokenizer_sha256)):
        raise ValueError("pinned base and tokenizer versions/SHA-256 are required")

    def snapshot(session_id: str) -> IdentitySnapshot:
        version = policy_version(session_id)
        return IdentitySnapshot(
            policy_version=version, behavior_version=version,
            behavior_sha256=trainable_state_sha256(model, max_bytes=max_trainable_bytes),
            base_version=base_version, base_sha256=base_sha256,
            tokenizer_version=tokenizer_version, tokenizer_sha256=tokenizer_sha256,
        )

    return snapshot


def make_peft_active_session_provider(model: Any) -> Callable[[], str]:
    """Read PEFT's active adapter and reject ambiguous multi-adapter states."""
    def active() -> str:
        value = getattr(model, "active_adapters", None)
        if callable(value):
            value = value()
        if value is None:
            value = getattr(model, "active_adapter", None)
            if callable(value):
                value = value()
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str):
            return value[0]
        raise RuntimeError("PEFT model has no single unambiguous active adapter")
    return active

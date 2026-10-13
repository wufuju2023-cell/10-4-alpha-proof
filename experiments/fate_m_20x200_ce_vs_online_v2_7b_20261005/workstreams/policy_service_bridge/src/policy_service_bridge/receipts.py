"""Canonical hashing and atomic immutable publication for policy receipts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from shared_actor_bridge import BehaviorIdentity, GenerationParameters, generation_receipt_payload


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(ValueError):
    """A service receipt is missing, mutable, or internally inconsistent."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def payload_sha256(value: Mapping[str, Any]) -> str:
    return canonical_sha256({key: item for key, item in value.items() if key != "receipt_sha256"})


def text_sha256(value: str) -> str:
    if type(value) is not str:
        raise TypeError("text hash input must be a string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_request_seed(*, seed_namespace: str, session_id: str, sequence: int,
                        normalized_request_sha256: str,
                        actor_prompt_sha256: str) -> int:
    """Derive a request seed bound to both the Reap request and actor input."""
    if (not seed_namespace or not session_id or type(sequence) is not int or sequence < 1
            or not SHA256_RE.fullmatch(normalized_request_sha256)
            or not SHA256_RE.fullmatch(actor_prompt_sha256)):
        raise ValueError("invalid request seed binding")
    raw = (f"{seed_namespace}\0{session_id}\0{sequence}\0"
           f"{normalized_request_sha256}\0{actor_prompt_sha256}").encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % (2**63)


def _contained(root: Path, path: Path) -> Path:
    # pathlib.resolve() may add the Windows extended-length ``\\?\`` prefix
    # only after a concurrently-created destination exists. Normalize absolute
    # paths consistently before containment checks across that race.
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        if os.name == "nt":
            root_text, path_text = str(resolved_root), str(resolved)
            if root_text.startswith("\\\\?\\"):
                root_text = root_text[4:]
            if path_text.startswith("\\\\?\\"):
                path_text = path_text[4:]
            common = os.path.commonpath((root_text, path_text))
            if os.path.normcase(common) != os.path.normcase(root_text):
                raise ValueError("outside root")
        else:
            resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ReceiptError("receipt path escapes receipt root") from exc
    return resolved


def publish_immutable(root: Path, relative: Path, value: Mapping[str, Any]) -> Path:
    """Atomically publish one receipt without overwrite semantics.

    A hard-link from a fully fsync'd temporary inode gives both atomic visibility
    and exclusive-create behavior.  The temporary is always on the destination
    filesystem.
    """
    root = root.resolve()
    destination = _contained(root, root / relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value) + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix=".pending-", suffix=".json", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, destination)
        except FileExistsError:
            raise
        if os.name != "nt":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return destination
    finally:
        temp.unlink(missing_ok=True)


def validate_committed_receipt(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or value.get("schema_version") not in {"fate.policy_request.v2", "fate.policy_request.v3"}):
        raise ReceiptError("not a supported fate.policy_request receipt")
    if value.get("status") != "committed":
        raise ReceiptError("policy evidence is not committed")
    digest = value.get("receipt_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or digest != payload_sha256(value):
        raise ReceiptError("policy receipt hash mismatch")
    binding = value.get("proxy_binding")
    request = value.get("normalized_request")
    response = value.get("openai_response")
    if not isinstance(binding, dict) or not isinstance(request, dict) or not isinstance(response, dict):
        raise ReceiptError("policy receipt lacks request/response binding")
    if binding.get("normalized_request_sha256") != canonical_sha256(request):
        raise ReceiptError("normalized request hash mismatch")
    has_actor_prompt_binding = "prompt_binding" in value
    if has_actor_prompt_binding:
        try:
            incoming_prompt = request["messages"][0]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ReceiptError("normalized request lacks the incoming Reap prompt") from exc
        actor_prompt = value.get("actor_prompt")
        prompt_binding = value.get("prompt_binding")
        if (type(incoming_prompt) is not str or not incoming_prompt
                or value.get("incoming_prompt") != incoming_prompt
                or type(actor_prompt) is not str or not actor_prompt
                or value.get("prompt") != actor_prompt
                or not isinstance(prompt_binding, dict)
                or prompt_binding.get("normalized_request_sha256") != binding.get("normalized_request_sha256")
                or prompt_binding.get("incoming_prompt_sha256") != text_sha256(incoming_prompt)
                or prompt_binding.get("actor_prompt_sha256") != text_sha256(actor_prompt)
                or type(prompt_binding.get("transform_applied")) is not bool
                or (not prompt_binding.get("transform_applied")
                    and actor_prompt != incoming_prompt)):
            raise ReceiptError("incoming/actor prompt binding mismatch")
        try:
            expected_seed = derive_request_seed(
                seed_namespace=value["seed_namespace"], session_id=value["session_id"],
                sequence=value["request_sequence"],
                normalized_request_sha256=binding["normalized_request_sha256"],
                actor_prompt_sha256=prompt_binding["actor_prompt_sha256"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptError("request seed binding is invalid") from exc
        if value.get("request_seed") != expected_seed:
            raise ReceiptError("request seed is not bound to the actual actor prompt")
    if (not isinstance(value.get("seed_namespace"), str) or not value["seed_namespace"]
            or value.get("served_model_id") != request.get("model")
            or not isinstance(value.get("actor_config_sha256"), str)
            or not SHA256_RE.fullmatch(value["actor_config_sha256"])
            or not isinstance(value.get("tokenizer_lock_sha256"), str)
            or not SHA256_RE.fullmatch(value["tokenizer_lock_sha256"])):
        raise ReceiptError("seed/model/tokenizer request binding is invalid")
    response_bytes = canonical_bytes(response)
    if binding.get("response_body_sha256") != hashlib.sha256(response_bytes).hexdigest():
        raise ReceiptError("response body hash mismatch")
    identity = value.get("behavior_identity")
    if not isinstance(identity, dict) or binding.get("behavior_identity_sha256") != canonical_sha256(identity):
        raise ReceiptError("behavior identity binding mismatch")
    choices = value.get("candidates")
    if not isinstance(choices, list) or not choices:
        raise ReceiptError("policy receipt has no candidates")
    response_choices = response.get("choices")
    if not isinstance(response_choices, list) or len(response_choices) != len(choices):
        raise ReceiptError("response/receipt choice count differs")
    generation_receipt = value.get("generation_receipt")
    if (not isinstance(generation_receipt, dict)
            or not isinstance(generation_receipt.get("outputs"), list)
            or len(generation_receipt["outputs"]) != len(choices)
            or value.get("generation_receipt_sha256") != canonical_sha256(generation_receipt)
            or generation_receipt.get("request_id") != value.get("request_id")
            or generation_receipt.get("request_seed") != value.get("request_seed")
            or generation_receipt.get("behavior_identity") != identity
            or generation_receipt.get("generation_params_sha256") != value.get("generation_contract", {}).get("generation_params_sha256")):
        raise ReceiptError("immutable generation receipt binding is invalid")
    try:
        generation_contract = dict(value["generation_contract"])
        generation_contract.pop("generation_params_sha256")
        expected_generation_receipt = generation_receipt_payload(
            request_id=value["request_id"], request_seed=value["request_seed"],
            prompt_token_ids=value["prompt_token_ids"],
            generation=GenerationParameters(**generation_contract),
            identity=BehaviorIdentity(**identity), raw_samples=choices,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceiptError("generation receipt inputs do not match actor contract") from exc
    if expected_generation_receipt != generation_receipt:
        raise ReceiptError("generation receipt payload differs from raw service candidates")
    prompt_ids = value.get("prompt_token_ids")
    if not isinstance(prompt_ids, list) or not prompt_ids or any(type(token) is not int or token < 0 for token in prompt_ids):
        raise ReceiptError("invalid prompt token IDs")
    if (has_actor_prompt_binding and value.get("actor_prompt_token_ids") != prompt_ids):
        raise ReceiptError("actor prompt token IDs differ from captured generation")
    for index, (candidate, response_choice) in enumerate(zip(choices, response_choices, strict=True)):
        if not isinstance(candidate, dict) or candidate.get("candidate_index") != index:
            raise ReceiptError("candidate order/index drift")
        candidate_hash = candidate.get("service_candidate_sha256")
        if not isinstance(candidate_hash, str) or not SHA256_RE.fullmatch(candidate_hash):
            raise ReceiptError("service candidate identity hash is invalid")
        if (candidate.get("request_id") != value.get("request_id")
                or candidate.get("request_seed") != value.get("request_seed")
                or candidate.get("generation_params_sha256") != value.get("generation_contract", {}).get("generation_params_sha256")):
            raise ReceiptError("candidate request/seed/generation binding mismatch")
        raw_ids = candidate.get("raw_completion_token_ids")
        old = candidate.get("raw_completion_old_logprobs")
        sampling = candidate.get("raw_completion_sampling_logprobs")
        if (not isinstance(raw_ids, list) or not raw_ids or any(type(token) is not int or token < 0 for token in raw_ids)
                or not isinstance(old, list) or len(old) != len(raw_ids)
                or not isinstance(sampling, list) or len(sampling) != len(raw_ids)):
            raise ReceiptError("candidate raw token/unwarped/sampling log-prob evidence is incomplete")
        sample = generation_receipt.get("outputs", [])[index]
        if (sample.get("candidate_index") != index
                or sample.get("raw_completion_token_ids") != raw_ids
                or sample.get("raw_completion_old_logprobs") != old
                or sample.get("raw_completion_sampling_logprobs") != sampling
                or sample.get("service_candidate_sha256") != candidate.get("service_candidate_sha256")):
            raise ReceiptError("generation receipt raw sample identity drift")
        try:
            content = response_choice["message"]["content"]
            log_content = response_choice["logprobs"]["content"]
        except (KeyError, TypeError) as exc:
            raise ReceiptError("OpenAI response choice is incomplete") from exc
        if (content != candidate.get("returned_text") or len(log_content) != len(raw_ids)
                or response_choice.get("finish_reason") != candidate.get("finish_reason")
                or (has_actor_prompt_binding
                    and (response_choice.get("raw_sample_index") != index
                         or response_choice.get("service_candidate_sha256") != candidate_hash))):
            raise ReceiptError("returned text/token evidence drift")
        for token_id, logprob, token_entry in zip(raw_ids, old, log_content, strict=True):
            if token_entry.get("token_id") != token_id or token_entry.get("logprob") != logprob:
                raise ReceiptError("OpenAI token log-prob differs from saved raw evidence")
        rendered = ""
        response_stops = []
        for position, token_entry in enumerate(log_content, start=1):
            token_text = token_entry.get("token") if isinstance(token_entry, dict) else None
            if not isinstance(token_text, str):
                raise ReceiptError("OpenAI token text is invalid")
            rendered += token_text
            if rendered == content:
                response_stops.append(position)
        if len(response_stops) != 1:
            raise ReceiptError("OpenAI token stream cannot prove one response-content boundary")
    return value


def load_committed_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError("cannot read policy receipt") from exc
    return validate_committed_receipt(value)

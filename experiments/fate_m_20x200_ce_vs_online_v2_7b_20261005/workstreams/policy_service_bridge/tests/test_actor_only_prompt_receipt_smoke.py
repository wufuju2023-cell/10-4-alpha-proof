from __future__ import annotations

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from policy_service_bridge import IdentitySnapshot, InProcessPolicyService
from policy_service_bridge.receipts import canonical_bytes, text_sha256
from shared_actor_bridge import BehaviorIdentity, canonical_sha256
from shared_actor_bridge.hf_adapter import RawGeneration


HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / "scripts" / "actor_only_prompt_receipt_smoke.py"
CONFIG = HERE.parents[1] / "config" / "fate_m_003_v001.real_reap_request.v1.json"
CONFIG_SHA256 = "57eb286334dcfe45de6e89db6a42555a0bda23e366dff431fd6771105f04b0dc"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("actor_only_prompt_receipt_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTokenizer:
    eos_token_id = 99

    def __call__(self, text, add_special_tokens=True):
        assert add_special_tokens is True
        return {"input_ids": [1, len(text), 3]}

    def decode(self, ids, skip_special_tokens=False):
        return "".join("" if token == 99 and skip_special_tokens else
                       ("<eos>" if token == 99 else chr(token)) for token in ids)


IDENTITY = IdentitySnapshot(
    "formal-test", "formal-test", "a" * 64,
    "REAL-Prover-test", "b" * 64,
    "REAL-Prover-tokenizer-test", "d" * 64,
)


def fake_result(kwargs, index):
    ids = (65 + index % 26, 99)
    old = (-0.2 - index / 1000, -0.3)
    sampling = (-0.1 - index / 1000, -0.15)
    identity = kwargs["expected_identity"]
    identity_sha = canonical_sha256(asdict(identity))
    evidence = {
        "request_id": kwargs["request_id"],
        "candidate_index": index,
        "request_seed": kwargs["request_seed"],
        "prompt_token_ids": [1, len(kwargs["prompt"]), 3],
        "raw_completion_token_ids": list(ids),
        "raw_completion_old_logprobs": list(old),
        "raw_completion_sampling_logprobs": list(sampling),
        "finish_reason": "stop",
        "generation_params_sha256": kwargs["generation"].sha256,
        "behavior_identity_sha256": identity_sha,
    }
    return RawGeneration(
        kwargs["request_id"], index, kwargs["request_seed"],
        tuple(evidence["prompt_token_ids"]), ids, old, sampling, "stop",
        canonical_sha256(evidence), 0.01, 0.009,
    )


def test_pinned_incoming_artifact_is_self_consistent_and_hash_bound(tmp_path: Path):
    smoke = load_smoke_module()
    artifact = smoke.load_incoming_artifact(CONFIG, CONFIG_SHA256)
    assert artifact["incoming_prompt_sha256"] == text_sha256(
        artifact["normalized_request"]["messages"][0]["content"])
    with pytest.raises(ValueError, match="file hash mismatch"):
        smoke.load_incoming_artifact(CONFIG, "0" * 64)
    changed = json.loads(CONFIG.read_text(encoding="utf-8"))
    changed["normalized_request"]["messages"][0]["content"] += " "
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="normalized request hash mismatch"):
        smoke.load_incoming_artifact(changed_path, smoke.sha256_file(changed_path))


def test_actor_point_of_use_prompt_is_exactly_the_committed_receipt(tmp_path: Path):
    smoke = load_smoke_module()
    artifact = smoke.load_incoming_artifact(CONFIG, CONFIG_SHA256)
    tokenizer = FakeTokenizer()
    transformed_calls = []
    actor_calls = []
    active = [None]

    def transform(session_id, incoming_prompt):
        assert session_id == smoke.SESSION_ID
        assert text_sha256(incoming_prompt) == artifact["incoming_prompt_sha256"]
        root = smoke.parse_reap_tactic_state(incoming_prompt)
        prompt = f"official-qwen::{root}"
        transformed_calls.append({"actor_prompt": prompt,
                                  "actor_prompt_sha256": text_sha256(prompt)})
        return prompt

    def generator(**kwargs):
        assert kwargs["live_identity"]() == BehaviorIdentity(**asdict(IDENTITY))
        results = tuple(fake_result(kwargs, index)
                        for index in range(kwargs["generation"].num_return_sequences))
        actor_calls.append({
            "prompt": kwargs["prompt"],
            "prompt_sha256": text_sha256(kwargs["prompt"]),
            "prompt_token_ids": list(results[0].prompt_token_ids),
        })
        return results

    def activate(session_id):
        active[0] = session_id

    service = InProcessPolicyService(
        model=object(), tokenizer=tokenizer,
        receipt_root=tmp_path / "actor_receipts",
        identity_provider=lambda _session_id: IDENTITY,
        activate_session=activate,
        active_session_provider=lambda: active[0],
        model_transaction_lock=threading.Lock(),
        generation=smoke.GENERATION,
        seed_namespace="actor-only-test",
        served_model_id="REAL-Prover",
        actor_config_sha256="e" * 64,
        tokenizer_lock_sha256="d" * 64,
        prompt_transform=transform,
        generator=generator,
        heartbeat=lambda _event: None,
    )
    _body, receipt_path = service.handle(
        session_id=smoke.SESSION_ID,
        raw_body=canonical_bytes(artifact["normalized_request"]),
    )
    result = smoke.verify_point_of_use_receipt(
        receipt_path=receipt_path,
        tokenizer=tokenizer,
        artifact=artifact,
        transformed_calls=transformed_calls,
        actor_calls=actor_calls,
    )
    assert all(result["conditions"].values())
    assert result["candidate_count"] == 64
    assert result["actor_prompt_tokens"] == 3

    actor_calls[0]["prompt_token_ids"] = [999]
    with pytest.raises(RuntimeError, match="point-of-use prompt/receipt binding failed"):
        smoke.verify_point_of_use_receipt(
            receipt_path=receipt_path,
            tokenizer=tokenizer,
            artifact=artifact,
            transformed_calls=transformed_calls,
            actor_calls=actor_calls,
        )

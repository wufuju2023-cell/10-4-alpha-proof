from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import threading
import urllib.error
import urllib.request

import pytest

from policy_service_bridge import (
    CandidateObservation, IdentitySnapshot, InProcessPolicyService,
    load_committed_receipt, parse_reap_tactic_state, receipt_to_search_state,
    start_policy_server,
)
from policy_service_bridge.receipts import (ReceiptError, canonical_bytes,
                                            payload_sha256, text_sha256)
from shared_actor_bridge import GenerationParameters, canonical_sha256
from shared_actor_bridge.hf_adapter import RawGeneration


class FakeTokenizer:
    eos_token_id = 99

    def decode(self, ids, skip_special_tokens=False):
        parts = []
        for token in ids:
            if token == 99 and skip_special_tokens:
                continue
            parts.append("<eos>" if token == 99 else chr(token))
        return "".join(parts)


class FragmentedUtf8Tokenizer(FakeTokenizer):
    """Models byte-BPE tokens that decode non-compositionally in isolation."""

    def decode(self, ids, skip_special_tokens=False):
        values = list(ids)
        if values == [200]:
            return "�"
        if values == [201]:
            return "�"
        content = [token for token in values if token != 99]
        if content == [200, 201]:
            return "é"
        if values == [99]:
            return "" if skip_special_tokens else "<eos>"
        return super().decode(values, skip_special_tokens=skip_special_tokens)


IDENTITY = IdentitySnapshot("policy-0", "policy-0", "a" * 64, "real-prover", "b" * 64,
                            "tokenizer-v1", "d" * 64)


class FakeGenerator:
    def __init__(self):
        self.sessions = []
        self.prompts = []

    def __call__(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        assert kwargs["expected_identity"].behavior_sha256 == "a" * 64
        assert kwargs["live_identity"]().behavior_sha256 == "a" * 64
        # Mirrors shared_actor_bridge's transaction acquisition. The service
        # holds a non-reentrant lock, so this must be a no-op nested scope.
        with kwargs["model_transaction"]():
            kwargs["activate_behavior"]()
            seed = kwargs["request_seed"]
            request_id = kwargs["request_id"]
            count = kwargs["generation"].num_return_sequences
            out = []
            for index in range(count):
                out.append(self._sample(kwargs, request_id, seed, index))
            return tuple(out)

    @staticmethod
    def _sample(kwargs, request_id, seed, index):
            ids = (65 + index, 99)
            old, sampling = (-0.2 - index, -0.3), (-0.1 - index, -0.15)
            identity = kwargs["expected_identity"]
            evidence = {"request_id": request_id, "candidate_index": index, "request_seed": seed,
                        "prompt_token_ids": [1, 2, 3], "raw_completion_token_ids": list(ids),
                        "raw_completion_old_logprobs": list(old),
                        "raw_completion_sampling_logprobs": list(sampling), "finish_reason": "stop",
                        "generation_params_sha256": kwargs["generation"].sha256,
                        "behavior_identity_sha256": canonical_sha256({
                            "policy_version": identity.policy_version, "behavior_version": identity.behavior_version,
                            "behavior_sha256": identity.behavior_sha256, "base_version": identity.base_version,
                            "base_sha256": identity.base_sha256, "tokenizer_version": identity.tokenizer_version,
                            "tokenizer_sha256": identity.tokenizer_sha256})}
            return RawGeneration(request_id, index, seed, (1, 2, 3), ids, old, sampling, "stop",
                                 canonical_sha256(evidence), .01, .009)


class FragmentedUtf8Generator(FakeGenerator):
    @staticmethod
    def _sample(kwargs, request_id, seed, index):
        ids = (200, 201, 99)
        old, sampling = (-0.2, -0.25, -0.3), (-0.1, -0.12, -0.15)
        identity = kwargs["expected_identity"]
        evidence = {"request_id": request_id, "candidate_index": index, "request_seed": seed,
                    "prompt_token_ids": [1, 2, 3], "raw_completion_token_ids": list(ids),
                    "raw_completion_old_logprobs": list(old),
                    "raw_completion_sampling_logprobs": list(sampling), "finish_reason": "stop",
                    "generation_params_sha256": kwargs["generation"].sha256,
                    "behavior_identity_sha256": canonical_sha256({
                        "policy_version": identity.policy_version,
                        "behavior_version": identity.behavior_version,
                        "behavior_sha256": identity.behavior_sha256,
                        "base_version": identity.base_version,
                        "base_sha256": identity.base_sha256,
                        "tokenizer_version": identity.tokenizer_version,
                        "tokenizer_sha256": identity.tokenizer_sha256})}
        return RawGeneration(request_id, index, seed, (1, 2, 3), ids, old, sampling, "stop",
                             canonical_sha256(evidence), .01, .009)


def request(n=2):
    return {"model": "real-prover", "messages": [{"role": "user", "content": "goal"}],
            "n": n, "temperature": 1.5, "top_p": .9, "max_tokens": 8,
            "logprobs": True}


def service(tmp_path: Path, *, identity=lambda _: IDENTITY, activate=lambda _: None,
            events=None, prompt_transform=None, generator=None, tokenizer=None):
    active_session = [""]

    def activate_and_track(session_id):
        activate(session_id)
        active_session[0] = session_id

    return InProcessPolicyService(
        model=object(), tokenizer=tokenizer or FakeTokenizer(), receipt_root=tmp_path,
        identity_provider=identity, activate_session=activate_and_track,
        active_session_provider=lambda: active_session[0],
        model_transaction_lock=threading.Lock(),
        generation=GenerationParameters(1.5, .9, 8, 2), seed_namespace="test-v1",
        served_model_id="real-prover", actor_config_sha256="e" * 64,
        tokenizer_lock_sha256="d" * 64,
        prompt_transform=prompt_transform,
        generator=generator or FakeGenerator(),
        heartbeat=(events if events is not None else []).append,
    )


def test_success_commits_exact_openai_and_raw_evidence(tmp_path: Path):
    body, path = service(tmp_path).handle(session_id="s1", raw_body=canonical_bytes(request()))
    response = json.loads(body)
    receipt = load_committed_receipt(path)
    assert receipt["schema_version"] == "fate.policy_request.v2"
    assert response == receipt["openai_response"]
    assert [c["message"]["content"] for c in response["choices"]] == ["A", "B"]
    assert receipt["candidates"][0]["raw_completion_token_ids"] == [65, 99]
    assert receipt["candidates"][0]["raw_completion_old_logprobs"] == [-.2, -.3]
    assert receipt["candidates"][0]["raw_completion_sampling_logprobs"] == [-.1, -.15]
    assert receipt["generation_receipt_sha256"]
    assert response["choices"][0]["logprobs"]["content"][0]["token_id"] == 65
    assert receipt["proxy_binding"]["response_body_sha256"]
    assert receipt["incoming_prompt"] == receipt["actor_prompt"] == "goal"
    assert receipt["prompt_binding"]["incoming_prompt_sha256"] == text_sha256("goal")
    assert receipt["prompt_binding"]["actor_prompt_sha256"] == text_sha256("goal")
    assert receipt["actor_prompt_token_ids"] == [1, 2, 3]
    assert response["choices"][0]["raw_sample_index"] == 0
    assert (response["choices"][0]["service_candidate_sha256"]
            == receipt["candidates"][0]["service_candidate_sha256"])
    assert "tactic_token_span" not in response["choices"][0]
    assert path.parent == tmp_path.resolve() / "s1" / "policy_requests"


def test_byte_bpe_fragments_publish_a_compositional_exact_span_stream(tmp_path: Path):
    body, path = service(
        tmp_path, tokenizer=FragmentedUtf8Tokenizer(), generator=FragmentedUtf8Generator(),
    ).handle(session_id="s1", raw_body=canonical_bytes(request()))
    response = json.loads(body)
    choice = response["choices"][0]
    assert choice["message"]["content"] == "é"
    assert [item["token"] for item in choice["logprobs"]["content"]] == ["", "é", "<eos>"]
    assert "".join(item["token"] for item in choice["logprobs"]["content"][:2]) == "é"
    load_committed_receipt(path)


def test_prompt_transform_drives_actor_and_binds_receipt_and_seed(tmp_path: Path):
    seen = []
    generator = FakeGenerator()

    def transform(session_id, incoming_prompt):
        seen.append((session_id, incoming_prompt))
        return f"official::{session_id}::{incoming_prompt}"

    _, path = service(tmp_path / "one", prompt_transform=transform,
                      generator=generator).handle(
        session_id="s1", raw_body=canonical_bytes(request()))
    receipt = load_committed_receipt(path)
    assert seen == [("s1", "goal")]
    assert generator.prompts == ["official::s1::goal"]
    assert receipt["incoming_prompt"] == "goal"
    assert receipt["actor_prompt"] == "official::s1::goal"
    assert receipt["prompt"] == receipt["actor_prompt"]
    assert receipt["prompt_binding"] == {
        "normalized_request_sha256": receipt["proxy_binding"]["normalized_request_sha256"],
        "incoming_prompt_sha256": text_sha256("goal"),
        "actor_prompt_sha256": text_sha256("official::s1::goal"),
        "transform_applied": True,
    }

    _, other_path = service(
        tmp_path / "two", prompt_transform=lambda _sid, _prompt: "different-official-prompt",
    ).handle(session_id="s1", raw_body=canonical_bytes(request()))
    other = load_committed_receipt(other_path)
    assert other["proxy_binding"]["normalized_request_sha256"] == receipt["proxy_binding"]["normalized_request_sha256"]
    assert other["request_sequence"] == receipt["request_sequence"] == 1
    assert other["request_seed"] != receipt["request_seed"]


@pytest.mark.parametrize("bad", [None, "", "   ", 7])
def test_prompt_transform_invalid_output_fails_before_actor_or_receipt(tmp_path: Path, bad):
    generator = FakeGenerator()
    instance = service(tmp_path, prompt_transform=lambda _sid, _prompt: bad,
                       generator=generator)
    with pytest.raises(ValueError, match="prompt transform"):
        instance.handle(session_id="s1", raw_body=canonical_bytes(request()))
    assert generator.prompts == []
    assert not list(tmp_path.rglob("*.json"))


def test_actor_prompt_binding_tamper_fails_even_with_rehashed_receipt(tmp_path: Path):
    _, path = service(tmp_path, prompt_transform=lambda _sid, _prompt: "official").handle(
        session_id="s1", raw_body=canonical_bytes(request()))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["prompt_binding"]["actor_prompt_sha256"] = "f" * 64
    value["receipt_sha256"] = payload_sha256(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ReceiptError, match="prompt binding"):
        load_committed_receipt(path)


def test_strict_reap_prompt_parser_extracts_only_one_terminal_root_goal():
    prompt = ("User: Please generate a tactic in lean4 to solve the state.\n"
              "Here're some theorems that may be helpful:\n\nSTATE:\n"
              "x : Nat\n⊢ x = x\nTACTIC:\n\nAssistant:")
    assert parse_reap_tactic_state(prompt) == "x : Nat\n⊢ x = x"
    with pytest.raises(ValueError, match="pinned tactic envelope"):
        parse_reap_tactic_state(prompt.replace("STATE:", "State:"))
    with pytest.raises(ValueError, match="exactly one main goal"):
        parse_reap_tactic_state(prompt.replace("x : Nat", "⊢ True"))


def test_receipt_tamper_and_overwrite_fail_closed(tmp_path: Path):
    _, path = service(tmp_path).handle(session_id="s1", raw_body=canonical_bytes(request()))
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        from policy_service_bridge.receipts import publish_immutable
        publish_immutable(tmp_path, path.relative_to(tmp_path), {"bad": True})
    assert path.read_bytes() == original
    value = json.loads(original)
    value["candidates"][0]["raw_completion_token_ids"][0] += 1
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ReceiptError):
        load_committed_receipt(path)


def test_search_join_requires_real_facts_for_every_candidate(tmp_path: Path):
    _, path = service(tmp_path).handle(session_id="s1", raw_body=canonical_bytes(request()))
    observations = tuple(CandidateObservation(
        event_id=f"event-{i}", trajectory_id="traj", action=chr(65 + i),
        sample_indices=(i,), sample_tactic_token_spans=((0, 1),), survivor_sample_index=i,
        action_value=float(i), verifier_status="unresolved", executor_receipt_sha256="c" * 64,
        state_after_sha256=str(i + 1) * 64, depth=0, parent_event_id=None,
        execution_disposition="executed", lean_tactic_executions=1,
    ) for i in range(2))
    state = receipt_to_search_state(path, state_id="state-1", state_sha256="d" * 64,
                                    observations=observations)
    assert state.prompt_token_ids == (1, 2, 3)
    assert state.raw_samples[0].raw_completion_token_ids == (65, 99)
    assert state.candidates[0].action == "A"
    with pytest.raises(ValueError, match="every generated"):
        receipt_to_search_state(path, state_id="x", state_sha256="d" * 64,
                                observations=observations[:1])


def test_identity_change_during_request_returns_no_output_or_receipt(tmp_path: Path):
    calls = 0

    def identity(_):
        nonlocal calls
        calls += 1
        return IDENTITY if calls < 3 else IdentitySnapshot("policy-1", "policy-1", "e" * 64,
                                                            "real-prover", "b" * 64,
                                                            "tokenizer-v1", "d" * 64)

    with pytest.raises(RuntimeError, match="identity changed"):
        service(tmp_path, identity=identity).handle(session_id="s1", raw_body=canonical_bytes(request()))
    assert not list(tmp_path.rglob("*.json"))


def test_session_concurrency_isolated_and_model_activation_serialized(tmp_path: Path):
    active = []

    def activate(session_id):
        active.append(session_id)

    instance = service(tmp_path, activate=activate)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda sid: instance.handle(session_id=sid,
                          raw_body=canonical_bytes(request())), ["s1", "s2", "s1", "s2"]))
    paths = [result[1] for result in results]
    assert len(paths) == len(set(paths)) == 4
    assert {load_committed_receipt(path)["session_id"] for path in paths} == {"s1", "s2"}
    assert sorted(
        load_committed_receipt(path)["request_sequence"]
        for path in paths
        if path.parent.parent.name == "s1"
    ) == [1, 2]
    # The service and actor adapter both request exact activation while the
    # same outer non-reentrant lock is held.
    assert len(active) == 8


def test_http_health_strict_contract_and_fail_closed(tmp_path: Path):
    events = []
    server, thread = start_policy_server(service(tmp_path, events=events), port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.load(urllib.request.urlopen(base + "/health"))["ok"] is True
        req = urllib.request.Request(base + "/sessions/s1/policy/v1/chat/completions",
                                     data=canonical_bytes(request()),
                                     headers={"Content-Type": "application/json"}, method="POST")
        response = json.load(urllib.request.urlopen(req))
        assert len(response["choices"]) == 2
        bad = request()
        bad["temperature"] = .5
        req = urllib.request.Request(base + "/sessions/s1/policy/v1/chat/completions",
                                     data=canonical_bytes(bad), method="POST")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert any(item["event"] == "policy_service_ready" for item in events)
    assert any(item["event"] == "policy_request_committed" for item in events)


def test_restart_sequence_scans_existing_receipts(tmp_path: Path):
    first = service(tmp_path)
    _, one = first.handle(session_id="s1", raw_body=canonical_bytes(request()))
    second = service(tmp_path)
    _, two = second.handle(session_id="s1", raw_body=canonical_bytes(request()))
    assert load_committed_receipt(one)["request_sequence"] == 1
    assert load_committed_receipt(two)["request_sequence"] == 2


def test_long_generation_emits_structured_heartbeat(tmp_path: Path):
    events = []
    slow = FakeGenerator()

    def generate(**kwargs):
        time.sleep(.04)
        return slow(**kwargs)

    instance = InProcessPolicyService(
        model=object(), tokenizer=FakeTokenizer(), receipt_root=tmp_path,
        identity_provider=lambda _: IDENTITY, activate_session=lambda _: None,
            active_session_provider=lambda: "s1", model_transaction_lock=threading.Lock(),
            generation=GenerationParameters(1.5, .9, 8, 2), seed_namespace="pulse-v1",
            served_model_id="real-prover", actor_config_sha256="e" * 64,
            tokenizer_lock_sha256="d" * 64,
        generator=generate, heartbeat=events.append, heartbeat_interval_seconds=.01,
    )
    instance.handle(session_id="s1", raw_body=canonical_bytes(request()))
    pulse = [item for item in events if item["event"] == "policy_request_heartbeat"]
    assert pulse and pulse[0]["stage"] == "generate_and_unwarped_rescore"


def test_wrong_active_adapter_is_rejected_before_generation(tmp_path: Path):
    instance = InProcessPolicyService(
        model=object(), tokenizer=FakeTokenizer(), receipt_root=tmp_path,
        identity_provider=lambda _: IDENTITY, activate_session=lambda _: None,
        active_session_provider=lambda: "other", model_transaction_lock=threading.Lock(),
        generation=GenerationParameters(1.5, .9, 8, 2), seed_namespace="adapter-v1",
        served_model_id="real-prover", actor_config_sha256="e" * 64,
        tokenizer_lock_sha256="d" * 64,
        generator=FakeGenerator(), heartbeat=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="active PEFT adapter"):
        instance.handle(session_id="s1", raw_body=canonical_bytes(request()))
    assert not list(tmp_path.rglob("*.json"))


def test_many_sessions_can_share_one_fixed_behavior_adapter_alias(tmp_path: Path):
    active = [""]

    def activate(_session_id):
        active[0] = "formal-initial-r16-a32-seed20261004"

    instance = InProcessPolicyService(
        model=object(), tokenizer=FakeTokenizer(), receipt_root=tmp_path,
        identity_provider=lambda _sid: IDENTITY, activate_session=activate,
        active_session_provider=lambda: active[0],
        adapter_name_for_session=lambda _sid: "formal-initial-r16-a32-seed20261004",
        model_transaction_lock=threading.Lock(),
        generation=GenerationParameters(1.5, .9, 8, 2), seed_namespace="wave-001",
        served_model_id="real-prover", actor_config_sha256="e" * 64,
        tokenizer_lock_sha256="d" * 64, generator=FakeGenerator(), heartbeat=lambda _event: None,
    )
    first = instance.handle(session_id="family01", raw_body=canonical_bytes(request()))
    second = instance.handle(session_id="family20", raw_body=canonical_bytes(request()))
    assert first[1].parent.parent.name == "family01"
    assert second[1].parent.parent.name == "family20"

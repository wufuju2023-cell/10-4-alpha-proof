from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared_actor_bridge import (
    ActorContractError, BehaviorIdentity, CandidateEvidence, GenerationParameters,
    RawSampleEvidence, RequestCost, SearchStateEvidence, build_unsigned_envelope,
    canonical_sha256, generate_raw_candidates, generation_receipt_sha256,
    sign_execution_attestation, to_ce_receipt, to_online_v2_receipts,
    validate_unsigned_envelope, write_immutable_envelope,
)

H = {name: char * 64 for name, char in {
    "statement": "1", "initial": "2", "middle": "3", "terminal": "4",
    "actor": "5", "budget": "6", "tokenizer": "7", "behavior": "8",
    "base": "9", "lock": "c", "kernel": "d",
}.items()}


class TinyTokenizer:
    eos_token_id = 255
    pad_token_id = 0

    def encode(self, text, add_special_tokens=False):
        body = [10 + ord(char) for char in text]
        return ([1] + body) if add_special_tokens else body

    def decode(self, ids, **kwargs):
        del kwargs
        return "".join(chr(item - 10) for item in ids if item not in {0, 1, 255})


TOK = TinyTokenizer()
GEN = GenerationParameters(1.5, 0.9, 256, 64)
IDENTITY = BehaviorIdentity(
    "policy-0001", "policy-0001", H["behavior"], "base-real-prover", H["base"],
    "tokenizer-real-prover", H["tokenizer"],
)
VERIFY_ARGS = {"expected_verifier_id": "reap-lean-4.28",
               "expected_verifier_lock_sha256": H["lock"], "tokenizer": TOK}


def _raw(request_id, seed, prompt_ids, index, action, *, warped, prefix="~"):
    ids = tuple(TOK.encode(prefix) + TOK.encode(action) + [TOK.eos_token_id])
    old = tuple(-0.05 - position / 100 for position in range(len(ids)))
    sampling = tuple(warped - position / 1000 for position in range(len(ids)))
    evidence = {
        "request_id": request_id, "candidate_index": index, "request_seed": seed,
        "prompt_token_ids": list(prompt_ids), "raw_completion_token_ids": list(ids),
        "raw_completion_old_logprobs": list(old),
        "raw_completion_sampling_logprobs": list(sampling), "finish_reason": "stop",
        "generation_params_sha256": GEN.sha256,
        "behavior_identity_sha256": canonical_sha256(asdict(IDENTITY)),
    }
    return RawSampleEvidence(index, ids, old, sampling, "stop",
                             canonical_sha256(evidence), 0.001, 0.0005)


def _candidate(event, action, indices, *, depth, parent, after, status, spans=None):
    start = len(TOK.encode("~")); stop = start + len(TOK.encode(action))
    return CandidateEvidence(
        event, "trajectory-proof", action, depth, parent, indices,
        tuple(spans) if spans is not None else tuple((start, stop) for _ in indices), indices[0], float(2 - depth), status,
        canonical_sha256({"event": event, "status": status}), after, "executed", 1,
    )


def _state(request_id, state_id, state_hash, prompt, seed, specs, *, prefix_variant_indices=()):
    prompt_ids = tuple(TOK.encode(prompt, add_special_tokens=True))
    raw_by_index = {}; candidates = []
    for event, action, indices, depth, parent, after, status, warped in specs:
        spans = []
        for index in indices:
            prefix = "~ " if index in prefix_variant_indices else "~"
            start = len(TOK.encode(prefix)); stop = start + len(TOK.encode(action))
            spans.append((start, stop))
            raw_by_index[index] = _raw(request_id, seed, prompt_ids, index, action,
                                       warped=warped, prefix=prefix)
        candidates.append(_candidate(event, action, indices, depth=depth, parent=parent,
                                     after=after, status=status, spans=spans))
    raws = tuple(raw_by_index[index] for index in range(64))
    generation_hash = generation_receipt_sha256(
        request_id=request_id, request_seed=seed, prompt_token_ids=prompt_ids,
        generation=GEN, identity=IDENTITY, raw_samples=tuple(asdict(x) for x in raws),
    )
    cost = RequestCost(len(prompt_ids), sum(len(x.raw_completion_token_ids) for x in raws),
                       sum(x.lean_tactic_executions for x in candidates),
                       sum(x.wall_seconds for x in raws), sum(x.gpu_seconds for x in raws))
    return SearchStateEvidence(request_id, request_id, generation_hash, state_id, state_hash,
                               prompt, prompt_ids, seed, GEN.sha256, raws,
                               tuple(candidates), cost)


def unsigned(*, outcome="proof"):
    if outcome in {"proof", "disproof"}:
        terminal = "verified_proof" if outcome == "proof" else "verified_disproof"
        states = (
            _state("request-1", "state-1", H["initial"], "goal one", 1234, (
                ("event-good", "g", tuple(range(32)), 0, None, H["middle"], "unresolved", -0.2),
                ("event-bad", "b", tuple(range(32, 64)), 0, None, "b" * 64, "invalid_tactic", -1.2),
            ), prefix_variant_indices=(0,)),
            _state("request-2", "state-2", H["middle"], "goal two", 1235, (
                ("event-finish", "f", tuple(range(64)), 1, "event-good", H["terminal"], terminal, -0.3),
            )),
        ); selected = ("event-good", "event-finish")
    else:
        status = {"unsolved": "invalid_tactic", "timeout": "timeout",
                  "indeterminate": "unresolved", "infra_error": "infrastructure_error"}[outcome]
        states = (_state("request-1", "state-1", H["initial"], "goal", 1234, (
            ("event-only", "x", tuple(range(64)), 0, None, H["initial"], status, -0.4),
        )),); selected = ()
    return build_unsigned_envelope(
        problem_id="fate_m_001_v001", statement_sha256=H["statement"], wave_index=1,
        initial_state_sha256=H["initial"], outcome=outcome,
        actor_config_sha256=H["actor"], budget_config_sha256=H["budget"],
        tokenizer_lock_sha256=H["tokenizer"], identity=IDENTITY, generation=GEN,
        states=states, selected_event_ids=selected, tokenizer=TOK,
        eos_token_id=TOK.eos_token_id, receipt_id="actor-receipt-1", attempt_id="attempt-1")


def unsigned_with_two_terminal_executions():
    alternate = "a" * 64
    alternate_terminal = "e" * 64
    states = (
        _state("request-1", "state-1", H["initial"], "goal one", 1234, (
            ("event-good", "g", tuple(range(16)), 0, None, H["middle"], "unresolved", -0.2),
            ("event-alt", "a", tuple(range(16, 32)), 0, None, alternate, "unresolved", -0.4),
            ("event-bad", "b", tuple(range(32, 64)), 0, None, "b" * 64, "invalid_tactic", -1.2),
        ), prefix_variant_indices=(0, 16)),
        _state("request-2", "state-2", H["middle"], "goal two", 1235, (
            ("event-finish", "f", tuple(range(64)), 1, "event-good", H["terminal"],
             "verified_proof", -0.3),
        ), prefix_variant_indices=(0,)),
        _state("request-3", "state-3", alternate, "goal three", 1236, (
            ("event-alt-finish", "z", tuple(range(64)), 1, "event-alt", alternate_terminal,
             "verified_disproof", -0.5),
        ), prefix_variant_indices=(0,)),
    )
    return build_unsigned_envelope(
        problem_id="fate_m_001_v001", statement_sha256=H["statement"], wave_index=1,
        initial_state_sha256=H["initial"], outcome="proof",
        actor_config_sha256=H["actor"], budget_config_sha256=H["budget"],
        tokenizer_lock_sha256=H["tokenizer"], identity=IDENTITY, generation=GEN,
        states=states, selected_event_ids=("event-good", "event-finish"), tokenizer=TOK,
        eos_token_id=TOK.eos_token_id, receipt_id="actor-receipt-1", attempt_id="attempt-1")


def _keys():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw).hex()
    return private, public


def _execution_signed(receipt):
    private, public = _keys()
    return sign_execution_attestation(
        receipt, tokenizer=TOK, attester_id="reap-lean-4.28",
        attester_lock_sha256=H["lock"], private_key=private), private, public


def _proof_signed(receipt):
    executed, private, public = _execution_signed(receipt)
    verification = {
        "verifier_id": "reap-lean-4.28", "verifier_lock_sha256": H["lock"],
        "request_sha256": executed["request_sha256"], "statement_sha256": executed["statement_sha256"],
        "selected_path_sha256": canonical_sha256(executed["selected_path"]),
        "initial_state_sha256": executed["request"]["initial_state_sha256"],
        "final_state_sha256": executed["selected_path"][-1]["state_after_sha256"],
        "result": "verified", "kernel_exit_code": 0, "kernel_receipt_sha256": H["kernel"],
        "actor_config_sha256": executed["actor_config_sha256"],
        "budget_config_sha256": executed["budget_config_sha256"],
        "tokenizer_lock_sha256": executed["tokenizer_lock_sha256"],
        "cost_sha256": canonical_sha256(executed["cost"]),
        "actor_envelope_sha256": canonical_sha256({k: v for k, v in executed.items()
                                                     if k not in {"verification", "receipt_sha256"}}),
    }
    digest = canonical_sha256(verification)
    verification["verification_receipt_sha256"] = digest
    verification["signature_hex"] = private.sign(bytes.fromhex(digest)).hex()
    signed = deepcopy(executed); signed["verification"] = verification
    signed["receipt_sha256"] = canonical_sha256({k: v for k, v in signed.items() if k != "receipt_sha256"})
    return signed, public


def test_formal_request_binds_raw_order_duplicates_mapping_cost_and_warped_q(tmp_path: Path):
    envelope = unsigned(); validate_unsigned_envelope(envelope, tokenizer=TOK)
    state = envelope["search_states"][0]
    assert [x["candidate_index"] for x in state["raw_samples"]] == list(range(64))
    assert len({tuple(x["raw_completion_token_ids"]) for x in state["raw_samples"]}) == 3
    assert all(x["mapped_event_id"] == "event-good" for x in state["raw_samples"][:32])
    assert sum(x["is_survivor"] for x in state["raw_samples"]) == 2
    good, bad = state["candidates"]
    assert good["sample_multiplicity"] == 32
    assert bad["sample_multiplicity"] == 32
    assert "action_mask" not in good
    assert len(state["raw_samples"][0]["raw_completion_sampling_logprobs"]) == len(
        state["raw_samples"][0]["raw_completion_token_ids"]
    )
    assert good["sample_tactic_token_spans"][0] == [2, 3]
    assert envelope["cost"]["generated_tokens"] > 0 and envelope["cost"]["wall_seconds"] > 0
    assert write_immutable_envelope(tmp_path / "actor.json", envelope, tokenizer=TOK).is_file()


@pytest.mark.parametrize("mutation", ["missing_raw", "order", "seed", "service", "cost", "eos"])
def test_generation_request_adversarial_drift_is_rejected(mutation):
    envelope = unsigned(); state = envelope["search_states"][0]
    if mutation == "missing_raw": state["raw_samples"].pop()
    elif mutation == "order": state["raw_samples"][0]["candidate_index"] = 7
    elif mutation == "seed": state["request_seed"] += 1
    elif mutation == "service": state["raw_samples"][0]["service_candidate_sha256"] = "0" * 64
    elif mutation == "cost": state["request_cost"]["generated_tokens"] = 0
    else: state["raw_samples"][0]["raw_completion_token_ids"].append(7)
    state["search_payload_sha256"] = canonical_sha256({k: v for k, v in state.items() if k != "search_payload_sha256"})
    envelope["receipt_sha256"] = canonical_sha256({k: v for k, v in envelope.items() if k != "receipt_sha256"})
    with pytest.raises(ActorContractError): validate_unsigned_envelope(envelope, tokenizer=TOK)


def test_tokenizer_is_revalidated_before_conversion():
    signed, public = _proof_signed(unsigned())
    class WrongTokenizer(TinyTokenizer):
        def encode(self, text, add_special_tokens=False):
            return [42] + super().encode(text, add_special_tokens=add_special_tokens)
    with pytest.raises(ActorContractError):
        to_online_v2_receipts(signed, verifier_public_key_hex=public,
                              **{**VERIFY_ARGS, "tokenizer": WrongTokenizer()})


def test_proof_expands_full_raw_actions_and_keeps_tactic_as_execution_metadata():
    signed, public = _proof_signed(unsigned())
    assert to_ce_receipt(signed, verifier_public_key_hex=public, **VERIFY_ARGS) == signed
    searches, verifiers, paths = to_online_v2_receipts(signed, verifier_public_key_hex=public, **VERIFY_ARGS)
    candidate = searches[0].candidates[0]
    state = signed["search_states"][0]
    raw_index = candidate.raw_sample_indices[0]
    raw = state["raw_samples"][raw_index]
    prompt = tuple(state["prompt_token_ids"])
    completion = tuple(raw["raw_completion_token_ids"])
    assert candidate.input_ids == prompt + completion
    assert candidate.old_logprobs == (0.0,) * len(prompt) + tuple(raw["raw_completion_sampling_logprobs"])
    assert candidate.unwarped_old_logprobs == (0.0,) * len(prompt) + tuple(raw["raw_completion_old_logprobs"])
    assert candidate.action_mask == (False,) * len(prompt) + (True,) * len(completion)
    assert candidate.execution_event_id == raw["mapped_event_id"]
    assert candidate.sample_multiplicity == len(candidate.raw_sample_indices)
    assert candidate.finish_reason == raw["finish_reason"]
    assert searches[0].eos_convention == "per_candidate"
    assert len(verifiers) == 4 and paths[0].outcome == "proof"
    assert all(verifier.execution_event_id for verifier in verifiers)
    same_execution_rows = [item for search in searches for item in search.candidates
                           if item.execution_event_id == "event-good"]
    assert len(same_execution_rows) == 2
    assert {item.sample_multiplicity for item in same_execution_rows} == {1, 31}
    path_row = next(item for search in searches for item in search.candidates
                    if item.event_id == paths[0].event_ids[-1])
    assert path_row.execution_event_id == signed["selected_path"][-1]["event_id"]
    from alphaproof_online_v2_arm.builder import build_rollout_samples
    rollout = build_rollout_samples(searches, verifiers, paths)
    assert len(rollout) == sum(len(search.candidates) for search in searches)
    assert sum(sample.on_verified_solution_path for sample in rollout) == len(paths[0].event_ids)


def test_every_unique_terminal_execution_gets_one_survivor_path():
    signed, public = _proof_signed(unsigned_with_two_terminal_executions())
    searches, verifiers, paths = to_online_v2_receipts(
        signed, verifier_public_key_hex=public, **VERIFY_ARGS
    )
    candidates = {candidate.event_id: candidate
                  for search in searches for candidate in search.candidates}
    terminal_verifiers = [verifier for verifier in verifiers if verifier.terminal_verified]

    assert len(terminal_verifiers) == 2
    assert len(paths) == 2
    assert paths[0].receipt_id == f"path-{signed['receipt_id']}"
    assert {
        candidates[path.event_ids[-1]].execution_event_id for path in paths
    } == {"event-finish", "event-alt-finish"}
    assert [candidates[event_id].execution_event_id for event_id in paths[0].event_ids] == [
        step["event_id"] for step in signed["selected_path"]
    ]
    assert [candidates[event_id].execution_event_id for event_id in paths[1].event_ids] == [
        "event-alt", "event-alt-finish"
    ]

    execution_evidence = {
        candidate["event_id"]: candidate
        for state in signed["search_states"] for candidate in state["candidates"]
    }
    for path in paths:
        for event_id in path.event_ids:
            candidate = candidates[event_id]
            assert execution_evidence[candidate.execution_event_id]["survivor_sample_index"] in (
                candidate.raw_sample_indices
            )

    from alphaproof_online_v2_arm.builder import build_rollout_samples
    rollout = build_rollout_samples(searches, verifiers, paths)
    assert sum(sample.terminal_verified for sample in rollout) == 2
    assert sum(sample.on_verified_solution_path for sample in rollout) == 4


@pytest.mark.parametrize("outcome", ["unsolved", "timeout", "indeterminate"])
def test_signed_nonproof_outcomes_reach_online_without_fake_proof(outcome):
    signed, _, public = _execution_signed(unsigned(outcome=outcome))
    searches, verifiers, paths = to_online_v2_receipts(signed, verifier_public_key_hex=public, **VERIFY_ARGS)
    assert len(searches) == len(verifiers) == 1 and paths == ()
    with pytest.raises(ActorContractError):
        to_ce_receipt(signed, verifier_public_key_hex=public, **VERIFY_ARGS)


def test_infrastructure_error_is_signed_evidence_but_fail_closed_for_learning():
    signed, _, public = _execution_signed(unsigned(outcome="infra_error"))
    with pytest.raises(ActorContractError, match="fail-closed"):
        to_online_v2_receipts(signed, verifier_public_key_hex=public, **VERIFY_ARGS)


def test_execution_signature_and_executor_evidence_tampering_fail_closed():
    signed, _, public = _execution_signed(unsigned(outcome="unsolved")); bad = deepcopy(signed)
    bad["search_states"][0]["candidates"][0]["executor_receipt_sha256"] = "f" * 64
    bad["receipt_sha256"] = canonical_sha256({k: v for k, v in bad.items() if k != "receipt_sha256"})
    with pytest.raises(ActorContractError):
        to_online_v2_receipts(bad, verifier_public_key_hex=public, **VERIFY_ARGS)


def _identity():
    return BehaviorIdentity("v", "v", "f" * 64, "base", "e" * 64, "tok", "d" * 64)


def test_hf_atomic_capture_preserves_dual_logprobs_and_rng():
    import torch
    class Tokenizer:
        eos_token_id = 9; pad_token_id = 0
        def __call__(self, *args, **kwargs):
            return {"input_ids": torch.tensor([[1, 2]]), "attention_mask": torch.ones((1, 2), dtype=torch.long)}
    class Model(torch.nn.Module):
        def __init__(self): super().__init__(); self.anchor = torch.nn.Parameter(torch.zeros(()))
        def generate(self, **kwargs):
            if "generator" in kwargs: raise ValueError("model_kwargs ['generator'] not used")
            return SimpleNamespace(sequences=torch.cat((kwargs["input_ids"], torch.tensor([[3, 9]])), dim=1))
        def forward(self, input_ids, **kwargs):
            logits = torch.zeros((input_ids.shape[0], input_ids.shape[1], 16)) + self.anchor
            return SimpleNamespace(logits=logits)
    model=Model(); model.eval(); lock=threading.RLock(); identity=_identity(); activations=[]
    @contextmanager
    def transaction():
        with lock: yield
    before=torch.random.get_rng_state().clone()
    result=generate_raw_candidates(model=model,tokenizer=Tokenizer(),prompt="goal",
        generation=GenerationParameters(1.5,.9,4,1),request_id="r",request_seed=77,
        expected_identity=identity,model_transaction=transaction,
        activate_behavior=lambda:activations.append("v"),live_identity=lambda:identity,rescore_micro_batch=1)
    assert torch.equal(before,torch.random.get_rng_state()) and activations == ["v"]
    assert result[0].candidate_index == 0 and result[0].request_id == "r"
    assert result[0].raw_completion_old_logprobs != result[0].raw_completion_sampling_logprobs


def test_shared_lock_covers_generate_rescore_and_identity_recheck():
    import torch
    entered_forward=threading.Event(); release_forward=threading.Event(); second_generated=threading.Event()
    stages=[]; lock=threading.RLock(); identity=_identity()
    class Tokenizer:
        eos_token_id=9; pad_token_id=0
        def __call__(self,*args,**kwargs): return {"input_ids":torch.tensor([[1,2]]),"attention_mask":torch.ones((1,2),dtype=torch.long)}
    class Model(torch.nn.Module):
        def __init__(self): super().__init__(); self.anchor=torch.nn.Parameter(torch.zeros(())); self.generations=0
        def generate(self,**kwargs):
            self.generations += 1; stages.append(f"g{self.generations}")
            if self.generations == 2: second_generated.set()
            return SimpleNamespace(sequences=torch.cat((kwargs["input_ids"],torch.tensor([[3,9]])),dim=1))
        def forward(self,input_ids,**kwargs):
            stages.append("forward")
            if stages.count("forward") == 1: entered_forward.set(); release_forward.wait(2)
            return SimpleNamespace(logits=torch.zeros((input_ids.shape[0],input_ids.shape[1],16))+self.anchor)
    model=Model(); model.eval()
    @contextmanager
    def transaction():
        with lock: yield
    def invoke(name):
        return generate_raw_candidates(model=model,tokenizer=Tokenizer(),prompt="goal",
            generation=GenerationParameters(1.5,.9,4,1),request_id=name,request_seed=77,
            expected_identity=identity,model_transaction=transaction,
            activate_behavior=lambda:stages.append(f"activate-{name}"),live_identity=lambda:identity,rescore_micro_batch=1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invoke,"a"); assert entered_forward.wait(1)
        second=pool.submit(invoke,"b"); time.sleep(.05); assert not second_generated.is_set()
        release_forward.set(); first.result(); second.result()
    assert stages.index("activate-b") > stages.index("forward")

"""Formal canonical-v2 shared actor envelope.

Each state records two levels: the exact ordered n=64 raw outputs of one
generation request, and the unique executed tactic candidates.  Raw duplicates
are retained and mapped to their unique action; sampling mass is aggregated by
unique raw token sequence so repeated draws do not create q(a)^2 baseline bias.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib, json, math, re, uuid
from typing import Any, Mapping, Sequence

SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERIFIER_STATUSES = frozenset({"verified_proof", "verified_disproof", "invalid_tactic", "unresolved", "timeout", "infrastructure_error"})
OUTCOMES = frozenset({"proof", "disproof", "unsolved", "timeout", "indeterminate", "infra_error"})
DISPOSITIONS = frozenset({"executed", "parse_rejected"})

class ActorContractError(ValueError): pass

def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()

def payload_sha256(value: Mapping[str, Any], excluded: str = "receipt_sha256") -> str:
    return canonical_sha256({k: v for k, v in value.items() if k != excluded})

def actor_evidence_sha256(value: Mapping[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in value.items() if k not in {"receipt_sha256", "verification", "execution_attestation"}})

def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA256.fullmatch(value): raise ActorContractError(f"{label} must be lowercase SHA-256")
    return value

def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip(): raise ActorContractError(f"{label} must be non-empty text")
    return value

def _ids(value: Any, label: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or not value or any(type(x) is not int or x < 0 for x in value):
        raise ActorContractError(f"{label} must contain nonnegative integer token IDs")
    return list(value)

def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ActorContractError(f"{label} must be finite")
    return float(value)

@dataclass(frozen=True)
class BehaviorIdentity:
    policy_version: str; behavior_version: str; behavior_sha256: str
    base_version: str; base_sha256: str; tokenizer_version: str; tokenizer_sha256: str
    def validate(self) -> None:
        for name in ("policy_version", "behavior_version", "base_version", "tokenizer_version"): _text(getattr(self, name), name)
        if self.policy_version != self.behavior_version: raise ActorContractError("policy_version must equal frozen behavior_version")
        for name in ("behavior_sha256", "base_sha256", "tokenizer_sha256"): _sha(getattr(self, name), name)

@dataclass(frozen=True)
class GenerationParameters:
    temperature: float; top_p: float; max_new_tokens: int; num_return_sequences: int; do_sample: bool = True
    def validate(self) -> None:
        if self.do_sample is not True or _finite(self.temperature, "temperature") <= 0: raise ActorContractError("invalid sampling contract")
        if not 0 < _finite(self.top_p, "top_p") <= 1: raise ActorContractError("top_p must be in (0,1]")
        if type(self.max_new_tokens) is not int or self.max_new_tokens < 1 or type(self.num_return_sequences) is not int or self.num_return_sequences < 1:
            raise ActorContractError("generation counts must be positive integers")
    def validate_formal(self) -> None:
        self.validate()
        if (self.temperature, self.top_p, self.num_return_sequences) != (1.5, .9, 64) \
                or self.max_new_tokens not in {256, 512}:
            raise ActorContractError(
                "formal actor requires temperature=1.5, top_p=0.9, "
                "max_new_tokens in {256,512}, n=64"
            )
    @property
    def sha256(self) -> str: self.validate(); return canonical_sha256(asdict(self))

@dataclass(frozen=True)
class RequestCost:
    prompt_tokens: int; generated_tokens: int; lean_tactic_executions: int; wall_seconds: float; gpu_seconds: float
    def validate(self) -> None:
        for name in ("prompt_tokens", "generated_tokens", "lean_tactic_executions"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0: raise ActorContractError(f"{name} must be nonnegative integer")
        for name in ("wall_seconds", "gpu_seconds"):
            if _finite(getattr(self, name), name) < 0: raise ActorContractError(f"{name} must be nonnegative")

@dataclass(frozen=True)
class RawSampleEvidence:
    candidate_index: int; raw_completion_token_ids: tuple[int, ...]
    raw_completion_old_logprobs: tuple[float, ...]; raw_completion_sampling_logprobs: tuple[float, ...]
    finish_reason: str; service_candidate_sha256: str; wall_seconds: float; gpu_seconds: float

@dataclass(frozen=True)
class CandidateEvidence:
    event_id: str; trajectory_id: str; action: str; depth: int; parent_event_id: str | None
    sample_indices: tuple[int, ...]; sample_tactic_token_spans: tuple[tuple[int, int], ...]
    survivor_sample_index: int; action_value: float
    verifier_status: str; executor_receipt_sha256: str; state_after_sha256: str
    execution_disposition: str; lean_tactic_executions: int

@dataclass(frozen=True)
class SearchStateEvidence:
    receipt_id: str; generation_request_id: str; generation_receipt_sha256: str
    state_id: str; state_sha256: str; prompt: str; prompt_token_ids: tuple[int, ...]
    request_seed: int; generation_params_sha256: str; raw_samples: tuple[RawSampleEvidence, ...]
    candidates: tuple[CandidateEvidence, ...]; request_cost: RequestCost

def _normal(value: str) -> str: return " ".join(value.replace("\r\n", "\n").split())
def _decode(tok: Any, ids: Sequence[int]) -> str:
    try: return str(tok.decode(list(ids), skip_special_tokens=True, clean_up_tokenization_spaces=False))
    except TypeError: return str(tok.decode(list(ids)))

def _raw(sample: RawSampleEvidence, index: int, eos: int, maximum: int) -> dict[str, Any]:
    if sample.candidate_index != index: raise ActorContractError("raw sample indices must be contiguous ordered 0..63")
    ids = _ids(sample.raw_completion_token_ids, "raw ids"); old = list(sample.raw_completion_old_logprobs); warped = list(sample.raw_completion_sampling_logprobs)
    if len(ids) != len(old) or len(ids) != len(warped) or any(not math.isfinite(float(x)) for x in old + warped):
        raise ActorContractError("raw IDs and both logprob arrays must be equal-length finite arrays")
    eos_positions = [i for i, token in enumerate(ids) if token == eos]
    if sample.finish_reason == "stop":
        if eos_positions != [len(ids)-1]: raise ActorContractError("stop requires exactly one final EOS")
    elif sample.finish_reason == "length":
        if eos_positions or len(ids) != maximum: raise ActorContractError("length requires no EOS and max_new_tokens IDs")
    else: raise ActorContractError("finish_reason must be stop or length")
    _sha(sample.service_candidate_sha256, "service_candidate_sha256")
    wall = _finite(sample.wall_seconds, "wall_seconds"); gpu = _finite(sample.gpu_seconds, "gpu_seconds")
    if wall <= 0 or gpu < 0: raise ActorContractError("nonempty generation requires positive wall and nonnegative GPU cost")
    return {"candidate_index": index, "raw_completion_token_ids": ids,
            "raw_completion_old_logprobs": [float(x) for x in old], "raw_completion_sampling_logprobs": [float(x) for x in warped],
            "unwarped_sequence_logprob": float(sum(old)), "sampling_sequence_logprob": float(sum(warped)),
            "finish_reason": sample.finish_reason, "service_candidate_sha256": sample.service_candidate_sha256,
            "wall_seconds": wall, "gpu_seconds": gpu}

def generation_receipt_payload(*, request_id: str, request_seed: int,
    prompt_token_ids: Sequence[int], generation: GenerationParameters,
    identity: BehaviorIdentity, raw_samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Canonical service-side receipt for one ordered generation request."""
    prompt_ids = _ids(prompt_token_ids, "generation prompt_token_ids")
    generation.validate(); identity.validate(); _text(request_id, "request_id")
    if type(request_seed) is not int or request_seed < 0:
        raise ActorContractError("request_seed must be a nonnegative integer")
    outputs = []
    for index, sample in enumerate(raw_samples):
        if sample.get("candidate_index") != index:
            raise ActorContractError("generation receipt outputs must be ordered 0..n-1")
        outputs.append({
            "candidate_index": index,
            "raw_completion_token_ids": list(sample["raw_completion_token_ids"]),
            "raw_completion_old_logprobs": list(sample["raw_completion_old_logprobs"]),
            "raw_completion_sampling_logprobs": list(sample["raw_completion_sampling_logprobs"]),
            "finish_reason": sample["finish_reason"],
            "service_candidate_sha256": sample["service_candidate_sha256"],
            "wall_seconds": sample["wall_seconds"],
            "gpu_seconds": sample["gpu_seconds"],
        })
    return {
        "schema_version": 1,
        "request_id": request_id,
        "request_seed": request_seed,
        "prompt_token_ids": prompt_ids,
        "generation_parameters": asdict(generation),
        "generation_params_sha256": generation.sha256,
        "behavior_identity": asdict(identity),
        "behavior_identity_sha256": canonical_sha256(asdict(identity)),
        "output_count": len(outputs),
        "outputs": outputs,
        "service_cost": {
            "prompt_tokens": len(prompt_ids),
            "generated_tokens": sum(len(x["raw_completion_token_ids"]) for x in outputs),
            "wall_seconds": sum(float(x["wall_seconds"]) for x in outputs),
            "gpu_seconds": sum(float(x["gpu_seconds"]) for x in outputs),
        },
    }

def generation_receipt_sha256(**kwargs: Any) -> str:
    return canonical_sha256(generation_receipt_payload(**kwargs))

def _candidate(c: CandidateEvidence, prompt_ids: list[int], tok: Any, raw: list[dict[str, Any]], eos: int) -> dict[str, Any]:
    for name in ("event_id", "trajectory_id", "action"): _text(getattr(c, name), name)
    if type(c.depth) is not int or c.depth < 0 or (c.depth == 0 and c.parent_event_id is not None): raise ActorContractError("invalid depth/parent")
    if c.depth > 0: _text(c.parent_event_id, "parent_event_id")
    indices = list(c.sample_indices); spans = [list(x) for x in c.sample_tactic_token_spans]
    if (not indices or indices != sorted(set(indices))
            or any(type(x) is not int or x < 0 or x >= len(raw) for x in indices)):
        raise ActorContractError("invalid sample_indices")
    if len(spans) != len(indices) or any(len(span) != 2 or any(type(x) is not int for x in span) for span in spans):
        raise ActorContractError("each mapped raw sample requires one integer tactic span")
    if c.survivor_sample_index not in indices:
        raise ActorContractError("survivor_sample_index must name one mapped raw sample")
    action_ids = list(tok.encode(c.action, add_special_tokens=False))
    if not action_ids or eos in action_ids:
        raise ActorContractError("action must tokenize to non-EOS IDs")
    survivor_action_ids = None
    survivor_span = None
    for index, span in zip(indices, spans, strict=True):
        sample = raw[index]; ids = sample["raw_completion_token_ids"]
        start, stop = span
        if not 0 <= start < stop <= len(ids):
            raise ActorContractError("sample tactic span is outside the raw completion")
        if eos in ids[start:stop]:
            raise ActorContractError("learner tactic span must exclude EOS")
        # Byte/BPE tokenization is context-sensitive: encoding the standalone
        # action need not reproduce the token IDs observed inside the sampled
        # completion.  The receipt must preserve those exact sampled IDs and
        # prove that their decoded text is the executed action.
        if _normal(_decode(tok, ids[start:stop])) != _normal(c.action):
            raise ActorContractError("sample tactic span must decode to the executed action")
        if index == c.survivor_sample_index:
            survivor_action_ids = ids[start:stop]
            survivor_span = span
    if survivor_action_ids is None or survivor_span is None:
        raise ActorContractError("survivor tactic span is missing")
    if c.verifier_status not in VERIFIER_STATUSES or c.execution_disposition not in DISPOSITIONS: raise ActorContractError("invalid executor result")
    if type(c.lean_tactic_executions) is not int or c.lean_tactic_executions < 0: raise ActorContractError("invalid Lean cost")
    if c.execution_disposition == "executed" and c.lean_tactic_executions != 1: raise ActorContractError("executed tactic requires one Lean call")
    if c.execution_disposition == "parse_rejected" and (c.lean_tactic_executions != 0 or c.verifier_status != "invalid_tactic"):
        raise ActorContractError("parse rejection requires invalid_tactic and zero Lean calls")
    _sha(c.executor_receipt_sha256, "executor_receipt_sha256"); _sha(c.state_after_sha256, "state_after_sha256")
    return {"event_id": c.event_id, "trajectory_id": c.trajectory_id, "action": c.action, "action_token_ids": survivor_action_ids,
            "depth": c.depth, "parent_event_id": c.parent_event_id, "tactic_token_span": survivor_span,
            "sample_indices": indices, "sample_tactic_token_spans": spans, "sample_multiplicity": len(indices),
            "survivor_sample_index": c.survivor_sample_index,
            "action_value": _finite(c.action_value, "action_value"),
            "verifier_status": c.verifier_status, "terminal_verified": c.verifier_status in {"verified_proof", "verified_disproof"},
            "executor_receipt_sha256": c.executor_receipt_sha256, "state_after_sha256": c.state_after_sha256,
            "execution_disposition": c.execution_disposition, "lean_tactic_executions": c.lean_tactic_executions}

def build_unsigned_envelope(*, problem_id: str, statement_sha256: str, wave_index: int, initial_state_sha256: str,
    outcome: str, actor_config_sha256: str, budget_config_sha256: str, tokenizer_lock_sha256: str,
    identity: BehaviorIdentity, generation: GenerationParameters, states: Sequence[SearchStateEvidence],
    selected_event_ids: Sequence[str], tokenizer: Any, eos_token_id: int,
    receipt_id: str | None = None, attempt_id: str | None = None) -> dict[str, Any]:
    _text(problem_id, "problem_id")
    for value, label in ((statement_sha256,"statement"),(initial_state_sha256,"initial"),(actor_config_sha256,"actor"),(budget_config_sha256,"budget"),(tokenizer_lock_sha256,"tokenizer")): _sha(value,label)
    if type(wave_index) is not int or wave_index < 1 or outcome not in OUTCOMES: raise ActorContractError("invalid wave/outcome")
    if outcome in {"proof", "disproof"} and not selected_event_ids or outcome not in {"proof", "disproof"} and selected_event_ids:
        raise ActorContractError("selected path/outcome mismatch")
    if len(selected_event_ids) > 64: raise ActorContractError("verified path exceeds fixed 64-bin horizon")
    if type(eos_token_id) is not int or eos_token_id < 0: raise ActorContractError("invalid EOS")
    identity.validate(); generation.validate_formal()
    if identity.tokenizer_sha256 != tokenizer_lock_sha256: raise ActorContractError("live tokenizer identity differs from tokenizer lock")
    if not states: raise ActorContractError("search states required")
    generation_sha = generation.sha256; state_payloads=[]; event_index={}; seen_requests=set(); totals=RequestCost(0,0,0,0.,0.)
    for state in states:
        if state.receipt_id != state.generation_request_id: raise ActorContractError("receipt_id must equal generation_request_id")
        _text(state.generation_request_id,"request_id"); _sha(state.generation_receipt_sha256,"generation_receipt_sha256")
        if state.generation_request_id in seen_requests: raise ActorContractError("generation request reused")
        seen_requests.add(state.generation_request_id)
        if type(state.request_seed) is not int or state.request_seed < 0 or state.generation_params_sha256 != generation_sha: raise ActorContractError("request seed/params drift")
        _text(state.state_id,"state_id"); _sha(state.state_sha256,"state_sha256"); _text(state.prompt,"prompt")
        prompt_ids=_ids(state.prompt_token_ids,"prompt_ids")
        if prompt_ids != list(tokenizer.encode(state.prompt,add_special_tokens=True)): raise ActorContractError("prompt token drift")
        if len(state.raw_samples) != 64: raise ActorContractError("formal request must retain all 64 raw samples")
        raw=[_raw(item,index,eos_token_id,generation.max_new_tokens) for index,item in enumerate(state.raw_samples)]
        identity_sha = canonical_sha256(asdict(identity))
        for item in raw:
            candidate_evidence = {
                "request_id": state.generation_request_id,
                "candidate_index": item["candidate_index"],
                "request_seed": state.request_seed,
                "prompt_token_ids": prompt_ids,
                "raw_completion_token_ids": item["raw_completion_token_ids"],
                "raw_completion_old_logprobs": item["raw_completion_old_logprobs"],
                "raw_completion_sampling_logprobs": item["raw_completion_sampling_logprobs"],
                "finish_reason": item["finish_reason"],
                "generation_params_sha256": generation_sha,
                "behavior_identity_sha256": identity_sha,
            }
            if item["service_candidate_sha256"] != canonical_sha256(candidate_evidence):
                raise ActorContractError("service candidate receipt does not bind request/output/identity")
        generation_payload = generation_receipt_payload(
            request_id=state.generation_request_id,
            request_seed=state.request_seed,
            prompt_token_ids=prompt_ids,
            generation=generation,
            identity=identity,
            raw_samples=raw,
        )
        if state.generation_receipt_sha256 != canonical_sha256(generation_payload):
            raise ActorContractError("generation receipt hash mismatch")
        candidates=[_candidate(item,prompt_ids,tokenizer,raw,eos_token_id) for item in state.candidates]
        if not candidates or len({x["action"] for x in candidates}) != len(candidates) or len({x["event_id"] for x in candidates}) != len(candidates): raise ActorContractError("unique candidates required")
        if sorted(index for item in candidates for index in item["sample_indices"]) != list(range(64)): raise ActorContractError("sample mapping must partition 0..63")
        for item in candidates:
            if item["event_id"] in event_index: raise ActorContractError("duplicate global event_id")
            event_index[item["event_id"]]=(item,state)
            for sample_index in item["sample_indices"]:
                raw[sample_index]["mapped_event_id"] = item["event_id"]
                raw[sample_index]["is_survivor"] = sample_index == item["survivor_sample_index"]
        expected=RequestCost(len(prompt_ids),sum(len(x["raw_completion_token_ids"]) for x in raw),sum(x["lean_tactic_executions"] for x in candidates),sum(x["wall_seconds"] for x in raw),sum(x["gpu_seconds"] for x in raw))
        state.request_cost.validate()
        if state.request_cost != expected or expected.generated_tokens <= 0 or expected.wall_seconds <= 0: raise ActorContractError("request cost/evidence mismatch")
        totals=RequestCost(totals.prompt_tokens+expected.prompt_tokens,totals.generated_tokens+expected.generated_tokens,totals.lean_tactic_executions+expected.lean_tactic_executions,totals.wall_seconds+expected.wall_seconds,totals.gpu_seconds+expected.gpu_seconds)
        body={"receipt_id":state.receipt_id,"generation_request_id":state.generation_request_id,"generation_receipt_sha256":state.generation_receipt_sha256,
              "state_id":state.state_id,"state_sha256":state.state_sha256,"prompt":state.prompt,"prompt_token_ids":prompt_ids,"request_seed":state.request_seed,
              "generation_params_sha256":generation_sha,"generation_receipt":generation_payload,
              "candidate_set_complete":True,"expected_raw_sample_count":64,"raw_samples":raw,
              "expected_candidate_count":len(candidates),"candidates":candidates,"request_cost":asdict(expected)}
        body["search_payload_sha256"]=canonical_sha256(body); state_payloads.append(body)
    selected=[]; previous=initial_state_sha256
    state_by_request={s["generation_request_id"]:s for s in state_payloads}
    for index,event_id in enumerate(selected_event_ids):
        if event_id not in event_index: raise ActorContractError("selected event absent")
        c,state=event_index[event_id]
        if state.state_sha256 != previous or c["depth"] != index or c["parent_event_id"] != (selected_event_ids[index-1] if index else None): raise ActorContractError("selected path chain drift")
        raw=state_by_request[state.generation_request_id]["raw_samples"][c["survivor_sample_index"]]
        after=event_index[selected_event_ids[index+1]][1].state_sha256 if index+1<len(selected_event_ids) else c["state_after_sha256"]
        selected.append({"step_index":index,"event_id":event_id,"search_receipt_id":state.receipt_id,"state_before_sha256":previous,"state_after_sha256":after,
                         "prompt":state.prompt,"prompt_token_ids":list(state.prompt_token_ids),"raw_completion_token_ids":raw["raw_completion_token_ids"],
                         "tactic_token_span":c["tactic_token_span"],"action":c["action"],"value_target":-float(len(selected_event_ids)-index)})
        previous=after
    all_statuses = [candidate["verifier_status"] for state in state_payloads for candidate in state["candidates"]]
    if outcome == "proof" and event_index[selected_event_ids[-1]][0]["verifier_status"] != "verified_proof":
        raise ActorContractError("proof outcome requires a verified_proof terminal survivor")
    if outcome == "disproof" and event_index[selected_event_ids[-1]][0]["verifier_status"] != "verified_disproof":
        raise ActorContractError("disproof outcome requires a verified_disproof terminal survivor")
    if outcome not in {"proof", "disproof"} and any(x in {"verified_proof", "verified_disproof"} for x in all_statuses):
        raise ActorContractError("nonterminal outcome cannot contain a verified terminal candidate")
    if outcome == "timeout" and "timeout" not in all_statuses:
        raise ActorContractError("timeout outcome requires timeout executor evidence")
    if outcome == "indeterminate" and "unresolved" not in all_statuses:
        raise ActorContractError("indeterminate outcome requires unresolved executor evidence")
    if outcome == "infra_error" and "infrastructure_error" not in all_statuses:
        raise ActorContractError("infra_error outcome requires infrastructure_error evidence")
    request={"problem_id":problem_id,"statement_sha256":statement_sha256,"wave_index":wave_index,"actor_config_sha256":actor_config_sha256,
             "budget_config_sha256":budget_config_sha256,"initial_state_sha256":initial_state_sha256,"generation_params_sha256":generation_sha,
             "behavior_identity_sha256":canonical_sha256(asdict(identity))}
    envelope={"schema_version":2,"receipt_id":receipt_id or f"actor-{uuid.uuid4().hex}","attempt_id":attempt_id or f"attempt-{uuid.uuid4().hex}",
              "problem_id":problem_id,"statement_sha256":statement_sha256,"outcome":outcome,"actor_config_sha256":actor_config_sha256,"budget_config_sha256":budget_config_sha256,
              "tokenizer_lock_sha256":tokenizer_lock_sha256,"behavior_identity":asdict(identity),"generation_contract":{**asdict(generation),"generation_params_sha256":generation_sha},
              "eos_token_id":eos_token_id,"request":request,"request_sha256":canonical_sha256(request),"search_states":state_payloads,"selected_path":selected,
              "cost":asdict(totals),"execution_attestation":None,"verification":None}
    envelope["receipt_sha256"]=payload_sha256(envelope)
    return envelope

def validate_unsigned_envelope(envelope: Mapping[str, Any], *, tokenizer: Any, permit_execution_attestation: bool=False) -> None:
    if not isinstance(envelope,Mapping) or envelope.get("schema_version") != 2 or envelope.get("verification") not in (None,{}): raise ActorContractError("not unsigned canonical v2")
    if not permit_execution_attestation and envelope.get("execution_attestation") not in (None,{}): raise ActorContractError("unexpected execution attestation")
    if envelope.get("receipt_sha256") != payload_sha256(envelope): raise ActorContractError("envelope hash mismatch")
    identity=BehaviorIdentity(**envelope["behavior_identity"]); generation_raw=dict(envelope["generation_contract"]); generation_raw.pop("generation_params_sha256",None); generation=GenerationParameters(**generation_raw)
    states=[]
    for state in envelope.get("search_states",[]):
        body={k:v for k,v in state.items() if k!="search_payload_sha256"}
        if state.get("search_payload_sha256") != canonical_sha256(body): raise ActorContractError("search state hash mismatch")
        raw=tuple(RawSampleEvidence(x["candidate_index"],tuple(x["raw_completion_token_ids"]),tuple(x["raw_completion_old_logprobs"]),tuple(x["raw_completion_sampling_logprobs"]),x["finish_reason"],x["service_candidate_sha256"],x["wall_seconds"],x["gpu_seconds"]) for x in state["raw_samples"])
        candidates=tuple(CandidateEvidence(
            x["event_id"], x["trajectory_id"], x["action"], x["depth"], x["parent_event_id"],
            tuple(x["sample_indices"]), tuple(tuple(span) for span in x["sample_tactic_token_spans"]),
            x["survivor_sample_index"], x["action_value"], x["verifier_status"],
            x["executor_receipt_sha256"], x["state_after_sha256"],
            x["execution_disposition"], x["lean_tactic_executions"],
        ) for x in state["candidates"])
        states.append(SearchStateEvidence(state["receipt_id"],state["generation_request_id"],state["generation_receipt_sha256"],state["state_id"],state["state_sha256"],state["prompt"],tuple(state["prompt_token_ids"]),state["request_seed"],state["generation_params_sha256"],raw,candidates,RequestCost(**state["request_cost"])))
    rebuilt=build_unsigned_envelope(problem_id=envelope["problem_id"],statement_sha256=envelope["statement_sha256"],wave_index=envelope["request"]["wave_index"],initial_state_sha256=envelope["request"]["initial_state_sha256"],outcome=envelope["outcome"],actor_config_sha256=envelope["actor_config_sha256"],budget_config_sha256=envelope["budget_config_sha256"],tokenizer_lock_sha256=envelope["tokenizer_lock_sha256"],identity=identity,generation=generation,states=states,selected_event_ids=tuple(x["event_id"] for x in envelope["selected_path"]),tokenizer=tokenizer,eos_token_id=envelope["eos_token_id"],receipt_id=envelope["receipt_id"],attempt_id=envelope["attempt_id"])
    rebuilt["execution_attestation"]=envelope.get("execution_attestation"); rebuilt["verification"]=envelope.get("verification"); rebuilt["receipt_sha256"]=payload_sha256(rebuilt)
    if canonical_bytes(rebuilt) != canonical_bytes(dict(envelope)): raise ActorContractError("envelope not derivable from canonical evidence")

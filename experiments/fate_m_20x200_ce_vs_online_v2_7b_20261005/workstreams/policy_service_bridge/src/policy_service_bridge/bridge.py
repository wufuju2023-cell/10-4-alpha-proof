"""Join immutable policy generation evidence with Reap/search-owned facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .receipts import load_committed_receipt
from shared_actor_bridge import (CandidateEvidence, RawSampleEvidence, RequestCost,
                                 SearchStateEvidence)


@dataclass(frozen=True)
class CandidateObservation:
    event_id: str
    trajectory_id: str
    action: str
    sample_indices: tuple[int, ...]
    sample_tactic_token_spans: tuple[tuple[int, int], ...]
    survivor_sample_index: int
    action_value: float
    verifier_status: str
    executor_receipt_sha256: str
    state_after_sha256: str
    depth: int
    parent_event_id: str | None
    execution_disposition: str
    lean_tactic_executions: int


def receipt_to_search_state(receipt_path: Path, *, state_id: str, state_sha256: str,
                            observations: tuple[CandidateObservation, ...]) -> SearchStateEvidence:
    """Join every raw sample to search mappings; never manufacture executor facts."""
    receipt = load_committed_receipt(receipt_path)
    generated = receipt["candidates"]
    raw_samples = tuple(RawSampleEvidence(
        candidate_index=item["candidate_index"],
        raw_completion_token_ids=tuple(item["raw_completion_token_ids"]),
        raw_completion_old_logprobs=tuple(item["raw_completion_old_logprobs"]),
        raw_completion_sampling_logprobs=tuple(item["raw_completion_sampling_logprobs"]),
        finish_reason=item["finish_reason"],
        service_candidate_sha256=item["service_candidate_sha256"],
        wall_seconds=item["wall_seconds"], gpu_seconds=item["gpu_seconds"],
    ) for item in generated)
    mapped = [index for observation in observations for index in observation.sample_indices]
    if sorted(mapped) != list(range(len(generated))):
        raise ValueError("search mappings must partition every generated raw sample")
    built = []
    for observed in observations:
        for index, span in zip(observed.sample_indices, observed.sample_tactic_token_spans, strict=True):
            start, stop = span
            if not 0 <= start < stop <= len(generated[index]["raw_completion_token_ids"]):
                raise ValueError("search-supplied tactic span is outside raw completion")
        built.append(CandidateEvidence(
            event_id=observed.event_id, trajectory_id=observed.trajectory_id,
            action=observed.action, depth=observed.depth,
            parent_event_id=observed.parent_event_id,
            sample_indices=observed.sample_indices,
            sample_tactic_token_spans=observed.sample_tactic_token_spans,
            survivor_sample_index=observed.survivor_sample_index,
            action_value=observed.action_value,
            verifier_status=observed.verifier_status,
            executor_receipt_sha256=observed.executor_receipt_sha256,
            state_after_sha256=observed.state_after_sha256,
            execution_disposition=observed.execution_disposition,
            lean_tactic_executions=observed.lean_tactic_executions,
        ))
    total_generated = sum(len(item.raw_completion_token_ids) for item in raw_samples)
    return SearchStateEvidence(
        receipt_id=receipt["request_id"],
        generation_request_id=receipt["request_id"],
        generation_receipt_sha256=receipt["generation_receipt_sha256"],
        state_id=state_id, state_sha256=state_sha256,
        prompt=receipt.get("actor_prompt", receipt["prompt"]),
        prompt_token_ids=tuple(receipt["prompt_token_ids"]),
        request_seed=receipt["request_seed"],
        generation_params_sha256=receipt["generation_contract"]["generation_params_sha256"],
        raw_samples=raw_samples, candidates=tuple(built),
        request_cost=RequestCost(
            prompt_tokens=len(receipt["prompt_token_ids"]), generated_tokens=total_generated,
            lean_tactic_executions=sum(item.lean_tactic_executions for item in observations),
            wall_seconds=sum(item.wall_seconds for item in raw_samples),
            gpu_seconds=sum(item.gpu_seconds for item in raw_samples),
        ),
    )

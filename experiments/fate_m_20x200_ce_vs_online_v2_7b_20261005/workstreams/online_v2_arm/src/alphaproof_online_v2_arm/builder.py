"""Construct learner samples only from validated immutable receipts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import fields
import math
from typing import Any, Iterable, overload

import torch

from .buffer import compute_search_advantages
from .receipts import (
    ProofPathReceipt,
    ReceiptInvariantError,
    SearchStateReceipt,
    VerifierReceipt,
    canonical_sha256,
    ordered_proof_chain_sha256,
)
from .rewards import VerificationRecord, strict_local_reward
from .schema import RolloutSample, SearchObservation


_BUILDER_TOKEN = object()


def _effective_action_value(
    search_action_value: float,
    verifier: VerifierReceipt | VerificationRecord,
    *,
    invalid_penalty: float = -0.1,
) -> float:
    """Return the action return justified at the receipt boundary.

    Search Q is meaningful for an unresolved successor.  Once the same action
    has a content-bound verifier result, however, its terminal return is known
    exactly and must override a pre-execution/default search value.  This is
    especially important for the formal one-step Reap capture, whose event
    start records all carried ``action_value=0`` even for later verified proof
    and invalid-tactic outcomes.
    """

    status = verifier.status
    terminal_verified = verifier.terminal_verified
    if status in {"verified_proof", "verified_disproof"}:
        if not terminal_verified:
            raise ReceiptInvariantError("positive terminal return lacks strict verification")
        return 1.0
    if terminal_verified:
        raise ReceiptInvariantError("only proof/disproof may have a terminal return")
    if status == "invalid_tactic":
        if not -1.0 <= invalid_penalty <= 0.0:
            raise ReceiptInvariantError("invalid penalty must be in [-1,0]")
        return float(invalid_penalty)
    if status in {"timeout", "infrastructure_error"}:
        return 0.0
    if status == "unresolved":
        return float(search_action_value)
    raise ReceiptInvariantError(f"unknown verifier status: {status}")


def _sample_payload(sample: RolloutSample) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(sample):
        value = getattr(sample, field.name)
        if isinstance(value, torch.Tensor):
            payload[field.name] = {
                "dtype": str(value.dtype),
                "shape": list(value.shape),
                "values": value.detach().cpu().tolist(),
            }
        else:
            payload[field.name] = value
    return payload


def _wave_sha256(samples: Sequence[RolloutSample]) -> str:
    return canonical_sha256({"samples": [_sample_payload(sample) for sample in samples]})


class ValidatedRolloutWave(Sequence[RolloutSample]):
    """A tamper-evident sequence constructible only by this builder module."""

    __slots__ = ("_samples", "_content_sha256")

    def __init__(self, samples: Iterable[RolloutSample], *, _token: object) -> None:
        if _token is not _BUILDER_TOKEN:
            raise TypeError("ValidatedRolloutWave can only be constructed by build_rollout_samples")
        self._samples = tuple(samples)
        self._content_sha256 = _wave_sha256(self._samples)

    @property
    def content_sha256(self) -> str:
        return self._content_sha256

    @property
    def reference_identity(self) -> dict[str, str]:
        """Frozen behavior/base identity carried by every receipt in the wave."""

        if not self._samples:
            raise ReceiptInvariantError("training wave is empty")
        sample = self._samples[0]
        return {
            "policy_version": sample.policy_version,
            "behavior_version": sample.behavior_version,
            "behavior_sha256": sample.behavior_sha256,
            "base_version": sample.base_version,
            "base_sha256": sample.base_sha256,
        }

    def __len__(self) -> int:
        return len(self._samples)

    @overload
    def __getitem__(self, index: int) -> RolloutSample: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[RolloutSample, ...]: ...

    def __getitem__(self, index: int | slice) -> RolloutSample | tuple[RolloutSample, ...]:
        return self._samples[index]

    def __iter__(self) -> Iterator[RolloutSample]:
        return iter(self._samples)

    def validate(self) -> tuple[RolloutSample, ...]:
        wave = validate_training_wave(self._samples)
        if self.content_sha256 != _wave_sha256(wave):
            raise ReceiptInvariantError("validated rollout wave content changed after construction")
        return wave


def validate_training_wave(
    samples: Iterable[RolloutSample], *, advantage_epsilon: float = 1e-6,
    advantage_clip: float = 5.0, tolerance: float = 1e-5,
) -> tuple[RolloutSample, ...]:
    """Revalidate receipt-derived invariants immediately before learning.

    This is deliberately redundant with :func:`build_rollout_samples`: it
    prevents a caller from bypassing the builder by constructing a plausible
    ``RolloutSample`` with an actor-supplied advantage or path flag.
    """

    wave = tuple(samples)
    if not wave:
        raise ReceiptInvariantError("training wave is empty")
    for sample in wave:
        sample.validate()
    if len({sample.wave_id for sample in wave}) != 1:
        raise ReceiptInvariantError("training samples mix wave_id values")
    if len({sample.policy_version for sample in wave}) != 1:
        raise ReceiptInvariantError("training samples mix policy versions")
    reference_identities = {
        (
            sample.behavior_version,
            sample.behavior_sha256.lower(),
            sample.base_version,
            sample.base_sha256.lower(),
        )
        for sample in wave
    }
    if len(reference_identities) != 1:
        raise ReceiptInvariantError("training samples mix behavior/base identities")
    if len({sample.tokenizer_sha256 for sample in wave}) != 1:
        raise ReceiptInvariantError("training samples mix tokenizer hashes")
    if len({sample.eos_token_id for sample in wave}) != 1:
        raise ReceiptInvariantError("training samples mix EOS token IDs")
    event_ids = [sample.event_id for sample in wave]
    if len(set(event_ids)) != len(event_ids):
        raise ReceiptInvariantError("training wave contains duplicate event_id")
    verifier_ids = [sample.verifier_receipt_id for sample in wave]
    if len(set(verifier_ids)) != len(verifier_ids):
        raise ReceiptInvariantError("verifier receipt is reused across events")

    state_groups: dict[tuple[str, str], list[RolloutSample]] = defaultdict(list)
    for sample in wave:
        state_groups[(sample.problem_id, sample.state_id)].append(sample)
    observations: list[SearchObservation] = []
    for (problem_id, state_id), group in state_groups.items():
        counts = {sample.state_candidate_count for sample in group}
        provenance = {(sample.search_receipt_id, sample.search_receipt_sha256) for sample in group}
        if counts != {len(group)}:
            raise ReceiptInvariantError(
                f"incomplete state candidate set at {problem_id}/{state_id}"
            )
        if len(provenance) != 1:
            raise ReceiptInvariantError("same state has mixed search receipt provenance")
        for sample in group:
            if (
                sample.on_verified_solution_path
                and sample.value_distance != 1.0
                and (sample.terminal_verified or sample.verifier_status != "unresolved")
            ):
                raise ReceiptInvariantError(
                    f"proof path {sample.proof_path_receipt_id} contains an invalid "
                    "non-terminal verifier status"
                )
            effective_value = _effective_action_value(
                sample.action_value,
                VerificationRecord(
                    sample.verifier_status,
                    sample.terminal_verified,
                    sample.verifier_receipt_id,
                ),
            )
            if not math.isclose(
                sample.action_value, effective_value, rel_tol=0.0, abs_tol=tolerance
            ):
                raise ReceiptInvariantError(
                    f"verifier-derived action return mismatch for event {sample.event_id}"
                )
            observations.append(SearchObservation(
                problem_id=sample.problem_id,
                state_id=sample.state_id,
                action=f"{sample.event_id}\0{sample.action_value}",
                action_value=sample.action_value,
                sample_multiplicity=sample.sample_multiplicity,
            ))
    expected = compute_search_advantages(
        observations, epsilon=advantage_epsilon, clip=advantage_clip
    )
    for sample in wave:
        baseline, advantage = expected[(
            sample.problem_id,
            sample.state_id,
            f"{sample.event_id}\0{sample.action_value}",
        )]
        if not math.isclose(sample.baseline_value, baseline, rel_tol=0.0, abs_tol=tolerance):
            raise ReceiptInvariantError(f"baseline mismatch for event {sample.event_id}")
        if not math.isclose(sample.advantage, advantage, rel_tol=0.0, abs_tol=tolerance):
            raise ReceiptInvariantError(f"advantage mismatch for event {sample.event_id}")
        expected_reward = strict_local_reward(VerificationRecord(
            sample.verifier_status, sample.terminal_verified, sample.verifier_receipt_id
        ))
        if not math.isclose(sample.local_reward, expected_reward, rel_tol=0.0, abs_tol=tolerance):
            raise ReceiptInvariantError(f"local reward mismatch for event {sample.event_id}")

    path_groups: dict[str, list[RolloutSample]] = defaultdict(list)
    for sample in wave:
        if sample.on_verified_solution_path:
            path_groups[str(sample.proof_path_receipt_id)].append(sample)
        elif sample.terminal_verified:
            raise ReceiptInvariantError("terminal event lacks verified proof-path membership")
    for path_id, group in path_groups.items():
        if len({sample.proof_path_receipt_sha256 for sample in group}) != 1:
            raise ReceiptInvariantError(f"proof path {path_id} has mixed content hashes")
        if len({(sample.problem_id, sample.trajectory_id) for sample in group}) != 1:
            raise ReceiptInvariantError(f"proof path {path_id} crosses problem/trajectory")
        distances = sorted(float(sample.value_distance) for sample in group)
        if distances != [float(index) for index in range(1, len(group) + 1)]:
            raise ReceiptInvariantError(f"proof path {path_id} has incomplete distance sequence")
        terminals = [sample for sample in group if sample.terminal_verified]
        if len(terminals) != 1 or terminals[0].value_distance != 1.0:
            raise ReceiptInvariantError(f"proof path {path_id} lacks one strict terminal event")
        nonterminals = [sample for sample in group if sample.value_distance != 1.0]
        if any(
            sample.terminal_verified or sample.verifier_status != "unresolved"
            for sample in nonterminals
        ):
            raise ReceiptInvariantError(
                f"proof path {path_id} contains an invalid non-terminal verifier status"
            )
    return wave


def _unique_by(items: Iterable[object], field: str, kind: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        key = str(getattr(item, field))
        if key in result:
            raise ReceiptInvariantError(f"duplicate {kind} {field}: {key}")
        result[key] = item
    return result


def build_rollout_samples(
    search_receipts: Iterable[SearchStateReceipt],
    verifier_receipts: Iterable[VerifierReceipt],
    proof_path_receipts: Iterable[ProofPathReceipt] = (),
    *,
    advantage_epsilon: float = 1e-6,
    advantage_clip: float = 5.0,
    invalid_penalty: float = -0.1,
) -> ValidatedRolloutWave:
    """Validate one complete wave and derive all mutable training targets.

    Baselines, standardized advantages, local rewards, proof-path membership,
    and remaining-step value targets are recomputed here.  None are accepted
    from an actor event, preventing partial candidate sets or forged success
    labels from entering the learner.
    """

    if advantage_epsilon != 1e-6 or advantage_clip != 5.0 or invalid_penalty != -0.1:
        raise ReceiptInvariantError(
            "advantage/reward constants are protocol-frozen at 1e-6, 5.0 and -0.1"
        )
    searches = list(search_receipts)
    verifiers = list(verifier_receipts)
    paths = list(proof_path_receipts)
    if not math.isclose(invalid_penalty, -0.1, rel_tol=0.0, abs_tol=0.0):
        raise ReceiptInvariantError("invalid_penalty is frozen at -0.1 for this experiment")
    if not searches:
        raise ReceiptInvariantError("a rollout wave requires search receipts")
    for item in searches:
        item.validate()
    for item in verifiers:
        item.validate()
    for item in paths:
        item.validate()

    wave_ids = {item.wave_id for item in searches}
    policy_versions = {item.policy_version for item in searches}
    reference_identities = {
        (
            item.behavior_version,
            item.behavior_sha256.lower(),
            item.base_version,
            item.base_sha256.lower(),
        )
        for item in searches
    }
    tokenizer_hashes = {item.tokenizer_sha256 for item in searches}
    eos_contracts = {(item.eos_token_id, item.eos_convention) for item in searches}
    if len(wave_ids) != 1:
        raise ReceiptInvariantError("builder input mixes wave_id values")
    if len(policy_versions) != 1:
        raise ReceiptInvariantError("builder input mixes frozen behavior policies")
    if len(reference_identities) != 1:
        raise ReceiptInvariantError("builder input mixes behavior/base identities")
    if len(tokenizer_hashes) != 1 or len(eos_contracts) != 1:
        raise ReceiptInvariantError("builder input mixes tokenizer/EOS contracts")
    wave_id = next(iter(wave_ids))
    if any(item.wave_id != wave_id for item in verifiers + paths):
        raise ReceiptInvariantError("verifier/proof receipt belongs to another wave")

    search_by_id = _unique_by(searches, "receipt_id", "search receipt")
    verifier_by_event = _unique_by(verifiers, "event_id", "verifier event")
    _unique_by(verifiers, "receipt_id", "verifier receipt")
    _unique_by(paths, "receipt_id", "proof-path receipt")

    event_index: dict[str, tuple[SearchStateReceipt, object]] = {}
    observations: list[SearchObservation] = []
    for search in searches:
        for candidate in search.candidates:
            if candidate.event_id in event_index:
                raise ReceiptInvariantError(f"duplicate event_id across wave: {candidate.event_id}")
            event_index[candidate.event_id] = (search, candidate)
    if set(verifier_by_event) != set(event_index):
        missing = sorted(set(event_index) - set(verifier_by_event))
        extra = sorted(set(verifier_by_event) - set(event_index))
        raise ReceiptInvariantError(
            f"verifier coverage must exactly match candidate events; missing={missing}, extra={extra}"
        )
    for event_id, verifier in verifier_by_event.items():
        search, _ = event_index[event_id]
        if (
            verifier.problem_id != search.problem_id
            or verifier.search_receipt_id != search.receipt_id
            or verifier.search_receipt_sha256 != search.receipt_sha256
        ):
            raise ReceiptInvariantError(f"verifier {verifier.receipt_id} is not bound to its search event")
        _, candidate = event_index[event_id]
        if (candidate.execution_event_id is not None
                and verifier.execution_event_id != candidate.execution_event_id):
            raise ReceiptInvariantError("raw-row verifier is not bound to its shared Lean execution event")

    effective_action_values: dict[str, float] = {}
    for event_id, (search, candidate) in event_index.items():
        verifier = verifier_by_event[event_id]
        effective_value = _effective_action_value(
            candidate.action_value, verifier, invalid_penalty=invalid_penalty
        )
        effective_action_values[event_id] = effective_value
        observations.append(SearchObservation(
            problem_id=search.problem_id,
            state_id=search.state_id,
            action=candidate.action,
            action_value=effective_value,
            sample_multiplicity=candidate.sample_multiplicity,
        ))

    path_for_event: dict[str, ProofPathReceipt] = {}
    terminal_execution_events_used: set[str] = set()
    path_execution_events_used: set[str] = set()
    for path in paths:
        candidates = []
        chain_entries: list[tuple[str, str, str, str, str]] = []
        for event_id in path.event_ids:
            if event_id not in event_index:
                raise ReceiptInvariantError(f"proof path references unknown event: {event_id}")
            search, candidate = event_index[event_id]
            if search.problem_id != path.problem_id:
                raise ReceiptInvariantError("proof path crosses problem boundaries")
            if event_id in path_for_event:
                raise ReceiptInvariantError(f"event occurs in multiple proof paths: {event_id}")
            candidates.append(candidate)
            path_for_event[event_id] = path
            verifier = verifier_by_event[event_id]
            chain_entries.append((
                event_id,
                search.receipt_id,
                search.receipt_sha256,
                verifier.receipt_id,
                verifier.receipt_sha256,
            ))
        if path.ordered_event_chain_sha256 != ordered_proof_chain_sha256(tuple(chain_entries)):
            raise ReceiptInvariantError("proof path ordered event/search/verifier chain mismatch")
        trajectory_ids = {item.trajectory_id for item in candidates}
        if len(trajectory_ids) != 1:
            raise ReceiptInvariantError("proof path crosses trajectories")
        for index, candidate in enumerate(candidates):
            expected_parent = None if index == 0 else candidates[index - 1].event_id
            if candidate.depth != index or candidate.parent_event_id != expected_parent:
                raise ReceiptInvariantError("proof path is not a continuous root-to-terminal chain")
        execution_chain = [item.execution_event_id or event_id
                           for item, event_id in zip(candidates, path.event_ids, strict=True)]
        if len(set(execution_chain)) != len(execution_chain) or any(
            event_id in path_execution_events_used for event_id in execution_chain
        ):
            raise ReceiptInvariantError("proof path reuses a Lean execution through multiple raw rows")
        path_execution_events_used.update(execution_chain)
        terminal = verifier_by_event[path.event_ids[-1]]
        expected_status = "verified_proof" if path.outcome == "proof" else "verified_disproof"
        if (
            terminal.receipt_id != path.terminal_verifier_receipt_id
            or terminal.receipt_sha256 != path.terminal_verifier_receipt_sha256
            or terminal.status != expected_status
            or not terminal.terminal_verified
        ):
            raise ReceiptInvariantError("proof path terminal does not match strict verifier receipt")
        terminal_execution_events_used.add(
            candidates[-1].execution_event_id or path.event_ids[-1]
        )
        for event_id in path.event_ids[:-1]:
            verifier = verifier_by_event[event_id]
            if verifier.terminal_verified or verifier.status != "unresolved":
                raise ReceiptInvariantError(
                    "non-terminal proof-path event must be a successfully applied unresolved state"
                )

    terminal_execution_events = {
        event_index[item.event_id][1].execution_event_id or item.event_id
        for item in verifiers if item.terminal_verified
    }
    if terminal_execution_events != terminal_execution_events_used:
        raise ReceiptInvariantError("every terminal verifier execution must have exactly one authentic proof path")

    advantages = compute_search_advantages(
        observations, epsilon=advantage_epsilon, clip=advantage_clip
    )
    samples: list[RolloutSample] = []
    for search in searches:
        for candidate in search.candidates:
            verifier = verifier_by_event[candidate.event_id]
            baseline, advantage = advantages[(search.problem_id, search.state_id, candidate.action)]
            path = path_for_event.get(candidate.event_id)
            value_distance = None
            if path is not None:
                value_distance = float(len(path.event_ids) - path.event_ids.index(candidate.event_id))
            sample = RolloutSample(
                problem_id=search.problem_id,
                trajectory_id=candidate.trajectory_id,
                state_id=search.state_id,
                policy_version=search.policy_version,
                behavior_version=search.behavior_version,
                behavior_sha256=search.behavior_sha256,
                base_version=search.base_version,
                base_sha256=search.base_sha256,
                wave_id=search.wave_id,
                event_id=candidate.event_id,
                prompt_len=len(search.prompt_token_ids),
                tokenizer_sha256=search.tokenizer_sha256,
                eos_token_id=search.eos_token_id,
                eos_convention=(
                    ("included_terminal" if candidate.finish_reason == "stop" else "excluded")
                    if search.eos_convention == "per_candidate" else search.eos_convention
                ),
                search_receipt_id=search.receipt_id,
                search_receipt_sha256=search.receipt_sha256,
                state_candidate_count=search.expected_candidate_count,
                input_ids=torch.tensor(candidate.input_ids, dtype=torch.long),
                attention_mask=torch.tensor(candidate.attention_mask, dtype=torch.long),
                action_mask=torch.tensor(candidate.action_mask, dtype=torch.bool),
                old_logprobs=torch.tensor(candidate.old_logprobs, dtype=torch.float32),
                action_value=effective_action_values[candidate.event_id],
                sample_multiplicity=candidate.sample_multiplicity,
                baseline_value=baseline,
                advantage=advantage,
                local_reward=strict_local_reward(VerificationRecord(
                    verifier.status, verifier.terminal_verified, verifier.receipt_id
                ), invalid_penalty=invalid_penalty),
                verifier_status=verifier.status,
                terminal_verified=verifier.terminal_verified,
                verifier_receipt_id=verifier.receipt_id,
                verifier_receipt_sha256=verifier.receipt_sha256,
                proof_path_receipt_id=None if path is None else path.receipt_id,
                proof_path_receipt_sha256=None if path is None else path.receipt_sha256,
                on_verified_solution_path=path is not None,
                value_distance=value_distance,
            )
            sample.validate()
            samples.append(sample)
    validate_training_wave(
        samples, advantage_epsilon=advantage_epsilon, advantage_clip=advantage_clip
    )
    return ValidatedRolloutWave(samples, _token=_BUILDER_TOKEN)

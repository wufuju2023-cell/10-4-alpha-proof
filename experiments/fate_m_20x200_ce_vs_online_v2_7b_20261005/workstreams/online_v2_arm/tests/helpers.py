from __future__ import annotations

from types import SimpleNamespace

import torch
from torch import nn

from alphaproof_online_v2_arm.objectives import (
    selected_action_logits,
    selected_warped_token_logprobs,
)
from alphaproof_online_v2_arm.builder import build_rollout_samples
from alphaproof_online_v2_arm.receipts import (
    CandidateReceipt,
    ProofPathReceipt,
    SearchStateReceipt,
    VerifierReceipt,
    ordered_proof_chain_sha256,
)
from alphaproof_online_v2_arm.schema import RolloutSample


class TinyPolicy(nn.Module):
    def __init__(self, vocab_size: int = 11, hidden_size: int = 8) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, hidden_size)
        self.projection = nn.Linear(hidden_size, vocab_size)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        output_hidden_states: bool = False,
        use_cache: bool = False,
    ):
        del attention_mask, use_cache
        hidden = self.embedding(input_ids)
        return SimpleNamespace(
            logits=self.projection(hidden),
            hidden_states=(hidden,) if output_hidden_states else None,
        )


def make_sample(
    behavior: nn.Module,
    *,
    problem_id: str,
    trajectory_id: str,
    policy_version: str = "policy-0001",
    action_value: float = 1.0,
    baseline_value: float = 0.0,
    local_reward: float = 0.0,
    verifier_status: str = "unresolved",
    terminal_verified: bool = False,
) -> RolloutSample:
    input_ids = torch.tensor(_supported_ids(behavior), dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    action_mask = torch.tensor([False, False, True, True])
    with torch.no_grad():
        logits = behavior(input_ids=input_ids.unsqueeze(0)).logits
        selected = selected_action_logits(logits, action_mask.unsqueeze(0))
        aligned = selected_warped_token_logprobs(
            selected, input_ids.unsqueeze(0), action_mask.unsqueeze(0),
            temperature=1.5, top_p=0.9,
        )
    old = torch.zeros_like(input_ids, dtype=torch.float32)
    old[action_mask] = aligned
    return RolloutSample(
        problem_id=problem_id,
        trajectory_id=trajectory_id,
        state_id=f"{problem_id}-state",
        policy_version=policy_version,
        behavior_version=policy_version,
        behavior_sha256="5" * 64,
        base_version="base-test",
        base_sha256="6" * 64,
        wave_id="test-wave-0001",
        event_id=f"event-{problem_id}-{trajectory_id}",
        prompt_len=2,
        tokenizer_sha256="1" * 64,
        eos_token_id=10,
        eos_convention="excluded",
        search_receipt_id=f"search-{problem_id}",
        search_receipt_sha256="2" * 64,
        state_candidate_count=1,
        input_ids=input_ids,
        attention_mask=attention_mask,
        action_mask=action_mask,
        old_logprobs=old,
        action_value=action_value,
        sample_multiplicity=1,
        baseline_value=baseline_value,
        advantage=action_value - baseline_value,
        local_reward=local_reward,
        verifier_status=verifier_status,
        terminal_verified=terminal_verified,
        verifier_receipt_id=f"receipt-{problem_id}-{trajectory_id}",
        verifier_receipt_sha256="3" * 64,
        proof_path_receipt_id=f"path-{problem_id}-{trajectory_id}",
        proof_path_receipt_sha256="4" * 64,
        on_verified_solution_path=True,
        value_distance=2.0,
    )


def _old_logprobs(model: nn.Module, ids: tuple[int, ...]) -> tuple[float, ...]:
    tensor = torch.tensor(ids, dtype=torch.long).unsqueeze(0)
    mask = torch.tensor([[False, False, True, True]])
    with torch.no_grad():
        selected = selected_action_logits(model(input_ids=tensor).logits, mask)
        aligned = selected_warped_token_logprobs(
            selected, tensor, mask, temperature=1.5, top_p=0.9,
        )
    return (0.0, 0.0, *(float(value) for value in aligned))


def _supported_ids(model: nn.Module, rank: int = 0) -> tuple[int, ...]:
    ids = [1, 2]
    with torch.no_grad():
        for _ in range(2):
            tensor = torch.tensor(ids, dtype=torch.long).unsqueeze(0)
            scores = model(input_ids=tensor).logits[0, -1].float() / 1.5
            sorted_scores, sorted_ids = scores.sort(descending=True)
            cumulative = sorted_scores.softmax(dim=-1).cumsum(dim=-1)
            remove = cumulative > 0.9
            remove[1:] = remove[:-1].clone(); remove[0] = False
            supported = sorted_ids[(~remove) & (sorted_ids != 10)]
            token = int(supported[min(rank, supported.numel() - 1)].item())
            ids.append(token)
    return tuple(ids)


def make_validated_wave(
    behavior: nn.Module,
    *,
    problem_ids: tuple[str, ...] = ("p1", "p2"),
    wave_id: str = "test-wave-0001",
    policy_version: str = "policy-0001",
    behavior_sha256: str = "5" * 64,
    base_version: str = "base-test",
    base_sha256: str = "6" * 64,
    verified_paths: bool = False,
    zero_policy_signal: bool = False,
):
    searches = []
    verifiers = []
    paths = []
    for problem_id in problem_ids:
        good_id = f"{problem_id}-good"
        bad_id = f"{problem_id}-bad"
        good_ids = _supported_ids(behavior)
        bad_ids = _supported_ids(behavior, rank=1)
        candidates = (
            CandidateReceipt(
                event_id=good_id,
                trajectory_id=f"trajectory-{problem_id}-good",
                action="good",
                depth=0,
                parent_event_id=None,
                input_ids=good_ids,
                attention_mask=(1, 1, 1, 1),
                action_mask=(False, False, True, True),
                old_logprobs=_old_logprobs(behavior, good_ids),
                action_value=0.0 if zero_policy_signal else 1.0,
                sample_multiplicity=1,
            ),
            CandidateReceipt(
                event_id=bad_id,
                trajectory_id=f"trajectory-{problem_id}-bad",
                action="bad",
                depth=0,
                parent_event_id=None,
                input_ids=bad_ids,
                attention_mask=(1, 1, 1, 1),
                action_mask=(False, False, True, True),
                old_logprobs=_old_logprobs(behavior, bad_ids),
                action_value=0.0,
                sample_multiplicity=1,
            ),
        )
        search = SearchStateReceipt.create(
            receipt_id=f"search-{problem_id}",
            problem_id=problem_id,
            wave_id=wave_id,
            policy_version=policy_version,
            behavior_version=policy_version,
            behavior_sha256=behavior_sha256,
            base_version=base_version,
            base_sha256=base_sha256,
            state_id=f"state-{problem_id}",
            tokenizer_sha256="1" * 64,
            eos_token_id=10,
            eos_convention="excluded",
            prompt_token_ids=(1, 2),
            candidate_set_complete=True,
            expected_candidate_count=2,
            candidates=candidates,
        )
        searches.append(search)
        good_status = "verified_proof" if verified_paths else "unresolved"
        bad_status = "unresolved" if zero_policy_signal else "invalid_tactic"
        for event_id, status in ((good_id, good_status), (bad_id, bad_status)):
            verifiers.append(
                VerifierReceipt.create(
                    receipt_id=f"verifier-{event_id}",
                    problem_id=problem_id,
                    wave_id=wave_id,
                    event_id=event_id,
                    search_receipt_id=search.receipt_id,
                    search_receipt_sha256=search.receipt_sha256,
                    status=status,
                    terminal_verified=status == "verified_proof",
                )
            )
        if verified_paths:
            terminal_verifier = next(
                item for item in verifiers if item.event_id == good_id
            )
            paths.append(
                ProofPathReceipt.create(
                    receipt_id=f"path-{problem_id}",
                    problem_id=problem_id,
                    wave_id=wave_id,
                    outcome="proof",
                    terminal_verifier_receipt_id=f"verifier-{good_id}",
                    terminal_verifier_receipt_sha256=terminal_verifier.receipt_sha256,
                    ordered_event_chain_sha256=ordered_proof_chain_sha256(((
                        good_id,
                        search.receipt_id,
                        search.receipt_sha256,
                        terminal_verifier.receipt_id,
                        terminal_verifier.receipt_sha256,
                    ),)),
                    event_ids=(good_id,),
                )
            )
    return build_rollout_samples(searches, verifiers, paths)

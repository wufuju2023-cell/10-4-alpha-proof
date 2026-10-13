from __future__ import annotations

from types import SimpleNamespace

import torch
from torch import nn

from alphaproof_online_v2.objectives import token_logprobs
from alphaproof_online_v2.schema import RolloutSample


class TinyPolicy(nn.Module):
    def __init__(self, vocab_size: int = 11, hidden_size: int = 8) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, hidden_size)
        self.projection = nn.Linear(hidden_size, vocab_size)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None):
        del attention_mask
        return SimpleNamespace(logits=self.projection(self.embedding(input_ids)))


def make_sample(
    model: nn.Module,
    *,
    problem_id: str,
    trajectory_id: str,
    policy_version: str = "policy-0001",
    advantage: float = 1.0,
) -> RolloutSample:
    input_ids = torch.tensor([1, 2, 3, 4], dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    action_mask = torch.tensor([False, False, True, True])
    with torch.no_grad():
        logits = model(input_ids=input_ids.unsqueeze(0)).logits
        aligned = token_logprobs(logits, input_ids.unsqueeze(0))[0]
    old = torch.zeros_like(input_ids, dtype=torch.float32)
    old[1:] = aligned
    return RolloutSample(
        problem_id=problem_id,
        trajectory_id=trajectory_id,
        state_id=f"{problem_id}-state",
        policy_version=policy_version,
        input_ids=input_ids,
        attention_mask=attention_mask,
        action_mask=action_mask,
        old_logprobs=old,
        advantage=advantage,
        verifier_status="verified",
        value_distance=2.0,
    )

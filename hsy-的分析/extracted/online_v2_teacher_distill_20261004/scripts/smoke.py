from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alphaproof_online_v2.learner import OnlineV2Config, OnlineV2Learner
from alphaproof_online_v2.objectives import token_logprobs
from alphaproof_online_v2.schema import RolloutSample
from alphaproof_online_v2.teacher import (
    OpenAICompatibleTeacher,
    TeacherConfig,
    VerificationDecision,
    collect_verified_distillation,
)


class TinyPolicy(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(13, 8)
        self.output = nn.Linear(8, 13)

    def forward(self, input_ids, attention_mask=None):
        del attention_mask
        return SimpleNamespace(logits=self.output(self.embedding(input_ids)))


def sample(model: nn.Module, problem_id: str, advantage: float) -> RolloutSample:
    ids = torch.tensor([1, 2, 3, 4])
    with torch.no_grad():
        aligned = token_logprobs(model(ids.unsqueeze(0)).logits, ids.unsqueeze(0))[0]
    old = torch.zeros(4)
    old[1:] = aligned
    return RolloutSample(
        problem_id=problem_id,
        trajectory_id=f"{problem_id}-trajectory",
        state_id=f"{problem_id}-state",
        policy_version="smoke-v1",
        input_ids=ids,
        attention_mask=torch.ones(4, dtype=torch.long),
        action_mask=torch.tensor([False, False, True, True]),
        old_logprobs=old,
        advantage=advantage,
        verifier_status="verified",
        value_distance=2.0,
    )


def main() -> None:
    torch.manual_seed(20261004)
    policy = TinyPolicy()
    reference = copy.deepcopy(policy)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=0.003)
    learner = OnlineV2Learner(
        policy,
        reference,
        optimizer,
        config=OnlineV2Config(
            update_epochs=1,
            min_independent_problems=2,
            target_kl=0.05,
            hard_kl_limit=0.5,
        ),
        canary=lambda _: True,
    )
    update = learner.update(
        [sample(reference, "p1", 1.0), sample(reference, "p2", 0.5)],
        policy_version="smoke-v1",
    )

    def transport(payload, headers, timeout):
        del payload, headers, timeout
        return {"choices": [{"message": {"content": '["exact h", "simp"]'}}]}

    teacher = OpenAICompatibleTeacher(
        TeacherConfig(endpoint="https://mock.invalid/v1/chat/completions"),
        transport=transport,
    )

    def verifier(state: str, action: str) -> VerificationDecision:
        del state
        if action == "exact h":
            return VerificationDecision(True, True, "smoke-proof")
        return VerificationDecision(True, False, diagnostic="not on verified proof path")

    examples, teacher_receipts = collect_verified_distillation(
        problem_id="teacher-p1",
        state_id="teacher-s1",
        state="h : P\n⊢ P",
        teacher=teacher,
        verifier=verifier,
        teacher_candidates=2,
    )
    output = {
        "status": "DONE",
        "online_update": update.to_dict(),
        "distillation_examples": [example.__dict__ for example in examples],
        "teacher_receipts": teacher_receipts,
        "note": "Synthetic CPU smoke only; no claim about Lean solve-rate improvement.",
    }
    output_path = ROOT / "runs" / "smoke" / "receipt.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()

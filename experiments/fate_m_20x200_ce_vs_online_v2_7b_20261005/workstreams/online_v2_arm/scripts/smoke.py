from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alphaproof_online_v2_arm.learner import OnlineV2Config, OnlineV2Learner
from alphaproof_online_v2_arm.builder import build_rollout_samples
from alphaproof_online_v2_arm.objectives import token_logprobs
from alphaproof_online_v2_arm.receipts import CandidateReceipt, SearchStateReceipt, VerifierReceipt


class TinyPolicy(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(13, 8)
        self.output = nn.Linear(8, 13)

    def forward(
        self, input_ids, attention_mask=None, output_hidden_states=False, use_cache=False
    ):
        del attention_mask, use_cache
        hidden = self.embedding(input_ids)
        return SimpleNamespace(
            logits=self.output(hidden),
            hidden_states=(hidden,) if output_hidden_states else None,
        )


def old_logprobs(model: nn.Module, ids: tuple[int, ...]) -> tuple[float, ...]:
    token_ids = torch.tensor(ids).unsqueeze(0)
    with torch.no_grad():
        aligned = token_logprobs(model(token_ids).logits, token_ids)[0]
    return (0.0, *(float(item) for item in aligned))


def make_wave(model: nn.Module):
    searches = []
    verifier_specs = []
    for problem_id in ("p1", "p2"):
        candidates = []
        for suffix, ids, value, status in (
            ("good", (1, 2, 3, 4), 1.0, "unresolved"),
            ("bad", (1, 2, 5, 6), 0.0, "invalid_tactic"),
        ):
            event_id = f"{problem_id}-{suffix}"
            candidates.append(CandidateReceipt(
                event_id=event_id,
                trajectory_id=f"trajectory-{event_id}",
                action=suffix,
                depth=0,
                parent_event_id=None,
                input_ids=ids,
                attention_mask=(1, 1, 1, 1),
                action_mask=(False, False, True, True),
                old_logprobs=old_logprobs(model, ids),
                action_value=value,
                sample_multiplicity=1,
            ))
            verifier_specs.append((problem_id, event_id, status))
        searches.append(SearchStateReceipt.create(
            receipt_id=f"search-{problem_id}",
            problem_id=problem_id,
            wave_id="smoke-wave-0001",
            policy_version="smoke-policy-v1",
            behavior_version="smoke-policy-v1",
            behavior_sha256="5" * 64,
            base_version="base-test",
            base_sha256="6" * 64,
            state_id=f"state-{problem_id}",
            tokenizer_sha256="1" * 64,
            eos_token_id=12,
            eos_convention="excluded",
            prompt_token_ids=(1, 2),
            candidate_set_complete=True,
            expected_candidate_count=2,
            candidates=tuple(candidates),
        ))
    search_for_event = {
        candidate.event_id: search
        for search in searches for candidate in search.candidates
    }
    verifiers = [VerifierReceipt.create(
        receipt_id=f"verifier-{event_id}",
        problem_id=problem_id,
        wave_id="smoke-wave-0001",
        event_id=event_id,
        search_receipt_id=search_for_event[event_id].receipt_id,
        search_receipt_sha256=search_for_event[event_id].receipt_sha256,
        status=status,
        terminal_verified=False,
    ) for problem_id, event_id, status in verifier_specs]
    return build_rollout_samples(searches, verifiers)


def main() -> None:
    torch.manual_seed(20261005)
    behavior = TinyPolicy()
    policy = copy.deepcopy(behavior)
    anchor = copy.deepcopy(behavior)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=0.003)
    with tempfile.TemporaryDirectory(prefix="online-v2-smoke-") as temp_dir:
        learner = OnlineV2Learner(
            policy,
            behavior,
            anchor,
            optimizer,
            behavior_policy_version="smoke-policy-v1",
            behavior_wave_id="smoke-wave-0001",
            ledger_path=Path(temp_dir) / "consumed-waves.json",
            config=OnlineV2Config(
                update_epochs=1,
                micro_batch_size=1,
                min_independent_problems=2,
                target_behavior_kl=0.05,
                hard_behavior_kl_limit=0.5,
                hard_anchor_kl_limit=0.8,
                value_coef=0.0,
            ),
            canary=lambda _: True,
        )
        receipt = learner.update(make_wave(behavior))
    output = {
        "status": "DONE" if receipt.accepted else "FAILED",
        "scope": "synthetic_cpu_only",
        "receipt": receipt.to_dict(),
        "claim_limit": "No real model, Lean, or solve-rate claim.",
    }
    path = ROOT / "runs" / "smoke" / "receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=True, indent=2), flush=True)
    if output["status"] != "DONE":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

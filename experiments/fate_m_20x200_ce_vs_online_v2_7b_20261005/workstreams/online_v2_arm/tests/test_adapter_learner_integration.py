from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import torch
import pytest
from torch import nn

from alphaproof_online_v2_arm.adapter_reference import (
    AdapterReferenceManifest,
    ArtifactIdentity,
    PeftSingleBackboneBackend,
    ReferenceIdentityError,
    SingleBackboneAdapterReferences,
    sha256_state_dict,
)
from alphaproof_online_v2_arm.learner import OnlineV2Config, OnlineV2Learner

from helpers import make_validated_wave


class TinyPeftLM(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(11, 8)
        self.projection = nn.Linear(8, 11)
        self.embedding.requires_grad_(False)
        self.projection.requires_grad_(False)
        self.policy_delta = nn.Parameter(torch.zeros(11))
        self.behavior_delta = torch.zeros(11)
        self.active_adapter: str | None = "policy"
        self.forward_training_modes: list[tuple[str | None, bool]] = []

    def set_adapter(self, name: str) -> None:
        if name not in {"policy", "behavior"}:
            raise KeyError(name)
        self.active_adapter = name

    @contextmanager
    def disable_adapter(self):
        previous = self.active_adapter
        self.active_adapter = None
        try:
            yield
        finally:
            self.active_adapter = previous

    def get_adapter_state_dict(self, adapter_name: str):
        if adapter_name != "behavior":
            raise KeyError(adapter_name)
        return {"delta": self.behavior_delta}

    def forward(
        self,
        input_ids,
        attention_mask=None,
        output_hidden_states=False,
        use_cache=False,
    ):
        del attention_mask, use_cache
        self.forward_training_modes.append((self.active_adapter, self.training))
        hidden = self.embedding(input_ids)
        logits = self.projection(hidden)
        if self.active_adapter == "policy":
            logits = logits + self.policy_delta
        elif self.active_adapter == "behavior":
            logits = logits + self.behavior_delta
        return SimpleNamespace(
            logits=logits,
            hidden_states=(hidden,) if output_hidden_states else None,
        )


def test_learner_uses_locked_single_backbone_three_views(tmp_path: Path) -> None:
    torch.manual_seed(29)
    model = TinyPeftLM()
    def live_base_state():
        return {
            "embedding": model.embedding.weight,
            "projection.weight": model.projection.weight,
            "projection.bias": model.projection.bias,
        }
    base = ArtifactIdentity("real-prover-test", sha256_state_dict(live_base_state()))
    backend = PeftSingleBackboneBackend(
        model,
        base_identity=base,
        adapter_versions={"policy": "policy-0001", "behavior": "policy-0001"},
        base_state_getter=live_base_state,
    )
    manifest = AdapterReferenceManifest(
        policy_adapter="policy",
        policy_version="policy-0001",
        behavior_adapter="behavior",
        behavior=backend.adapter_identity("behavior"),
        base=backend.base_identity(),
    )
    references = SingleBackboneAdapterReferences(backend, manifest)
    wave = make_validated_wave(
        references.behavior_reference,
        behavior_sha256=manifest.behavior.sha256,
        base_version=manifest.base.version,
        base_sha256=manifest.base.sha256,
    )
    optimizer = torch.optim.AdamW(references.policy_model.parameters(), lr=0.003)
    learner = OnlineV2Learner(
        references.policy_model,
        references.behavior_reference,
        references.base_reference,
        optimizer,
        behavior_policy_version="policy-0001",
        behavior_wave_id="test-wave-0001",
        ledger_path=tmp_path / "ledger.json",
        adapter_references=references,
        config=OnlineV2Config(
            update_epochs=1,
            micro_batch_size=1,
            min_independent_problems=2,
            target_behavior_kl=0.05,
            hard_behavior_kl_limit=0.5,
            hard_anchor_kl_limit=0.8,
            value_coef=0.0,
        ),
    )
    behavior_hash = backend.adapter_identity("behavior")
    before = model.policy_delta.detach().clone()
    model.forward_training_modes.clear()
    receipt = learner.update(wave)
    assert receipt.accepted
    assert model.active_adapter == "policy"
    assert not torch.equal(before, model.policy_delta)
    assert backend.adapter_identity("behavior") == behavior_hash
    assert model.forward_training_modes
    assert all(not training for _, training in model.forward_training_modes)


def _make_integrated_learner(tmp_path: Path):
    torch.manual_seed(31)
    model = TinyPeftLM()
    base_version = "real-prover-test"

    def live_base_state():
        return {
            "embedding": model.embedding.weight,
            "projection.weight": model.projection.weight,
            "projection.bias": model.projection.bias,
        }

    initial = ArtifactIdentity(base_version, sha256_state_dict(live_base_state()))
    backend = PeftSingleBackboneBackend(
        model,
        base_identity=initial,
        adapter_versions={"policy": "policy-0001", "behavior": "policy-0001"},
        base_state_getter=live_base_state,
    )
    manifest = AdapterReferenceManifest(
        policy_adapter="policy",
        policy_version="policy-0001",
        behavior_adapter="behavior",
        behavior=backend.adapter_identity("behavior"),
        base=initial,
    )
    references = SingleBackboneAdapterReferences(backend, manifest)
    wave = make_validated_wave(
        references.behavior_reference,
        behavior_sha256=manifest.behavior.sha256,
        base_version=manifest.base.version,
        base_sha256=manifest.base.sha256,
    )
    learner = OnlineV2Learner(
        references.policy_model,
        references.behavior_reference,
        references.base_reference,
        torch.optim.AdamW(references.policy_model.parameters(), lr=0.003),
        behavior_policy_version="policy-0001",
        behavior_wave_id="test-wave-0001",
        ledger_path=tmp_path / "identity-ledger.json",
        adapter_references=references,
        config=OnlineV2Config(update_epochs=1, micro_batch_size=1, value_coef=0.0),
    )
    return model, references, learner, wave


def test_learner_entry_calls_wave_identity_validation(tmp_path: Path, monkeypatch) -> None:
    _, references, learner, wave = _make_integrated_learner(tmp_path)
    calls = []
    original = references.validate_wave_identity

    def recording_validator(**identity):
        calls.append(identity)
        raise RuntimeError("identity-validator-called")

    monkeypatch.setattr(references, "validate_wave_identity", recording_validator)
    with pytest.raises(RuntimeError, match="identity-validator-called"):
        learner.update(wave)
    assert calls == [wave.reference_identity]
    monkeypatch.setattr(references, "validate_wave_identity", original)


def test_mutating_frozen_base_fails_before_learning(tmp_path: Path) -> None:
    model, _, learner, wave = _make_integrated_learner(tmp_path)
    with torch.no_grad():
        model.embedding.weight[0, 0].add_(1.0)
    with pytest.raises(ReferenceIdentityError, match="live base identity changed"):
        learner.update(wave)
    assert learner.optimizer_steps == 0

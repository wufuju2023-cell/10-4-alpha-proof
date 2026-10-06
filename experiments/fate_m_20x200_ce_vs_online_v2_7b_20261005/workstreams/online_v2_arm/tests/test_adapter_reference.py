from __future__ import annotations

from contextlib import contextmanager
import threading
import time

import pytest
import torch
from torch import nn

from alphaproof_online_v2_arm.adapter_reference import (
    AdapterReferenceManifest,
    ArtifactIdentity,
    PeftSingleBackboneBackend,
    ReferenceIdentityError,
    SingleBackboneAdapterReferences,
    sha256_state_dict,
)


class FakePeftModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.policy_weight = nn.Parameter(torch.tensor(2.0))
        self.behavior_weight = torch.tensor(3.0)
        self.active_adapter: str | None = "policy"
        self.fail_next = False

    def set_adapter(self, name: str) -> None:
        if name not in {"policy", "behavior"}:
            raise KeyError(name)
        self.active_adapter = name

    def get_adapter_state_dict(self, adapter_name: str):
        assert adapter_name == "behavior"
        return {"weight": self.behavior_weight}

    @contextmanager
    def disable_adapter(self):
        previous = self.active_adapter
        self.active_adapter = None
        try:
            yield
        finally:
            self.active_adapter = previous

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic forward failure")
        if self.active_adapter == "policy":
            scale = self.policy_weight
        elif self.active_adapter == "behavior":
            scale = self.behavior_weight
        else:
            scale = torch.tensor(1.0)
        return input_ids.float() * scale


class FakeBackend:
    def __init__(self) -> None:
        self._model = FakePeftModel()
        self.base = ArtifactIdentity("real-prover-fe76f68d", "a" * 64)
        self.behavior_version = "policy-0001"

    @property
    def model(self) -> FakePeftModel:
        return self._model

    def activate_adapter(self, name: str) -> None:
        self._model.set_adapter(name)

    def adapters_disabled(self):
        return self._model.disable_adapter()

    def adapter_identity(self, name: str) -> ArtifactIdentity:
        assert name == "behavior"
        return ArtifactIdentity(
            self.behavior_version,
            sha256_state_dict({"weight": self._model.behavior_weight}),
        )

    def base_identity(self) -> ArtifactIdentity:
        return self.base


def make_references() -> tuple[SingleBackboneAdapterReferences, FakeBackend]:
    backend = FakeBackend()
    manifest = AdapterReferenceManifest(
        policy_adapter="policy",
        policy_version="policy-0001",
        behavior_adapter="behavior",
        behavior=backend.adapter_identity("behavior"),
        base=backend.base_identity(),
    )
    return SingleBackboneAdapterReferences(backend, manifest), backend


def test_three_views_share_one_model_and_restore_policy() -> None:
    references, backend = make_references()
    inputs = torch.tensor([2.0])

    assert references.policy_model(input_ids=inputs).item() == 4.0
    assert references.behavior_reference(input_ids=inputs).item() == 6.0
    assert backend.model.active_adapter == "policy"
    assert references.base_reference(input_ids=inputs).item() == 2.0
    assert backend.model.active_adapter == "policy"
    parameters = list(references.policy_model.parameters())
    assert len(parameters) == 1 and parameters[0] is backend.model.policy_weight
    assert list(references.policy_model.named_children()) == []


@pytest.mark.parametrize("kind", ["policy", "behavior", "base"])
def test_forward_exception_restores_policy_and_training_mode(kind: str) -> None:
    references, backend = make_references()
    backend.model.train()
    backend.model.fail_next = True
    view = {
        "policy": references.policy_model,
        "behavior": references.behavior_reference,
        "base": references.base_reference,
    }[kind]

    with pytest.raises(RuntimeError, match="synthetic"):
        view(input_ids=torch.tensor([1.0]))

    assert backend.model.active_adapter == "policy"
    assert backend.model.training


def test_runtime_rejects_mutated_behavior_and_base_identity() -> None:
    references, backend = make_references()
    backend.model.behavior_weight.add_(1.0)
    with pytest.raises(ReferenceIdentityError, match="behavior adapter identity changed"):
        references.behavior_reference(input_ids=torch.tensor([1.0]))

    references, backend = make_references()
    backend.base = ArtifactIdentity("wrong-revision", "b" * 64)
    with pytest.raises(ReferenceIdentityError, match="base identity changed"):
        references.base_reference(input_ids=torch.tensor([1.0]))


def test_wave_receipt_identity_is_checked() -> None:
    references, _ = make_references()
    manifest = references.manifest
    references.validate_wave_identity(
        policy_version=manifest.policy_version,
        behavior_version=manifest.behavior.version,
        behavior_sha256=manifest.behavior.sha256.upper(),
        base_version=manifest.base.version,
        base_sha256=manifest.base.sha256.upper(),
    )
    with pytest.raises(ReferenceIdentityError, match="policy version mismatch"):
        references.validate_wave_identity(
            policy_version="policy-0000",
            behavior_version=manifest.behavior.version,
            behavior_sha256=manifest.behavior.sha256,
            base_version=manifest.base.version,
            base_sha256=manifest.base.sha256,
        )


def test_exclusive_update_blocks_rollout_switch_until_optimizer_phase_finishes() -> None:
    references, backend = make_references()
    entered_update = threading.Event()
    allow_update_to_finish = threading.Event()
    rollout_finished = threading.Event()

    def update() -> None:
        with references.exclusive_update():
            entered_update.set()
            assert allow_update_to_finish.wait(timeout=2)
            # Simulates backward/optimizer work after policy forward.
            assert backend.model.active_adapter == "policy"

    def rollout() -> None:
        assert entered_update.wait(timeout=2)
        references.behavior_reference(input_ids=torch.tensor([1.0]))
        rollout_finished.set()

    update_thread = threading.Thread(target=update)
    rollout_thread = threading.Thread(target=rollout)
    update_thread.start()
    rollout_thread.start()
    assert entered_update.wait(timeout=2)
    time.sleep(0.05)
    assert not rollout_finished.is_set()
    allow_update_to_finish.set()
    update_thread.join(timeout=2)
    rollout_thread.join(timeout=2)

    assert rollout_finished.is_set()
    assert backend.model.active_adapter == "policy"


def test_state_hash_is_key_order_independent_and_value_sensitive() -> None:
    first = {"b": torch.tensor([2]), "a": torch.tensor([1])}
    second = {"a": torch.tensor([1]), "b": torch.tensor([2])}
    changed = {"a": torch.tensor([1]), "b": torch.tensor([3])}
    assert sha256_state_dict(first) == sha256_state_dict(second)
    assert sha256_state_dict(first) != sha256_state_dict(changed)


def test_peft_backend_uses_live_adapter_state_and_dynamic_base_receipt() -> None:
    model = FakePeftModel()
    live_base = {"frozen.weight": torch.tensor([1.0])}
    base = ArtifactIdentity("base-v1", sha256_state_dict(live_base))
    backend = PeftSingleBackboneBackend(
        model,
        base_identity=base,
        adapter_versions={"policy": "policy-0001", "behavior": "policy-0001"},
        base_state_getter=lambda: live_base,
    )
    before = backend.adapter_identity("behavior")
    model.behavior_weight.add_(1.0)
    after = backend.adapter_identity("behavior")
    assert before.version == after.version == "policy-0001"
    assert before.sha256 != after.sha256
    live_base["frozen.weight"].add_(1.0)
    assert backend.base_identity().version == "base-v1"
    assert backend.base_identity().sha256 != base.sha256


def test_peft_backend_rejects_cached_only_base_identity() -> None:
    model = FakePeftModel()
    with pytest.raises(TypeError, match="base_state_getter is required"):
        PeftSingleBackboneBackend(
            model,
            base_identity=ArtifactIdentity("base-v1", "c" * 64),
            adapter_versions={"policy": "policy-0001", "behavior": "policy-0001"},
        )

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest
import torch

from alphaproof_online_v2_arm.real_runner import (
    JOIN_SCHEMA,
    RealRunnerError,
    _write_checkpoint_outputs,
    assert_only_adapter_trainable,
    load_frozen_config,
    load_join_receipts,
    require_behavior_adapter_name,
    sha256_file,
)
from alphaproof_online_v2_arm.receipts import (
    CandidateReceipt,
    SearchStateReceipt,
    VerifierReceipt,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _joined_fixture(tmp_path: Path):
    source_hash = "a" * 64
    source = tmp_path / "signed.json"
    _write_json(source, {"receipt_sha256": source_hash})
    candidate = CandidateReceipt(
        event_id="event-1", trajectory_id="trajectory-1", action="raw:one",
        depth=0, parent_event_id=None, input_ids=(1, 2, 3, 4),
        attention_mask=(1, 1, 1, 1), action_mask=(False, False, True, True),
        old_logprobs=(0.0, 0.0, -0.5, -0.25), action_value=1.0,
        sample_multiplicity=1,
    )
    search = SearchStateReceipt.create(
        receipt_id="search-1", problem_id="problem-1", wave_id="wave-0001",
        policy_version="policy-1", behavior_version="policy-1",
        behavior_sha256="b" * 64, base_version="base-1", base_sha256="c" * 64,
        state_id="state-1", tokenizer_sha256="d" * 64, eos_token_id=10,
        eos_convention="excluded", prompt_token_ids=(1, 2), candidate_set_complete=True,
        expected_candidate_count=1, candidates=(candidate,),
    )
    verifier = VerifierReceipt.create(
        receipt_id="verifier-1", problem_id="problem-1", wave_id="wave-0001",
        event_id="event-1", search_receipt_id=search.receipt_id,
        search_receipt_sha256=search.receipt_sha256, status="unresolved",
        terminal_verified=False,
    )
    joined = tmp_path / "online-v2-receipts.json"
    _write_json(joined, {
        "schema_version": JOIN_SCHEMA,
        "source_receipt_sha256": source_hash,
        "searches": [asdict(search)],
        "verifiers": [asdict(verifier)],
        "paths": [],
    })
    pin = {
        "path": str(joined), "sha256": sha256_file(joined),
        "source_receipt_sha256": source_hash,
        "source_receipt_path": str(source),
        "source_receipt_file_sha256": sha256_file(source),
    }
    return joined, source, pin


def test_load_join_receipts_roundtrips_nested_tuples_and_builds_wave(tmp_path: Path) -> None:
    _, _, pin = _joined_fixture(tmp_path)
    wave, evidence = load_join_receipts([pin])
    assert len(wave) == 1
    assert wave[0].input_ids.tolist() == [1, 2, 3, 4]
    assert wave[0].wave_id == "wave-0001"
    assert evidence[0]["source_receipt_file_sha256"] == pin["source_receipt_file_sha256"]


def test_load_join_receipts_rejects_unknown_top_level_and_source_domain_mixup(
    tmp_path: Path,
) -> None:
    joined, source, pin = _joined_fixture(tmp_path)
    payload = json.loads(joined.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    _write_json(joined, payload)
    pin["sha256"] = sha256_file(joined)
    with pytest.raises(RealRunnerError, match="unknown top-level"):
        load_join_receipts([pin])

    payload.pop("unexpected")
    _write_json(joined, payload)
    pin["sha256"] = sha256_file(joined)
    _write_json(source, {"receipt_sha256": "e" * 64})
    pin["source_receipt_file_sha256"] = sha256_file(source)
    with pytest.raises(RealRunnerError, match="does not carry"):
        load_join_receipts([pin])


def test_frozen_config_requires_exact_external_hash(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    _write_json(config, {
        "schema_version": "fate.online_v2.real_one_update.v1",
        "status": "frozen", "mode": "smoke", "pins": {}, "train": {},
    })
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    loaded, actual = load_frozen_config(config, digest)
    assert loaded["mode"] == "smoke"
    assert actual == digest
    with pytest.raises(RealRunnerError, match="config hash mismatch"):
        load_frozen_config(config, "0" * 64)


def test_only_selected_adapter_can_be_trainable() -> None:
    class TwoAdapters(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lora_A = torch.nn.ModuleDict({
                "policy": torch.nn.Linear(2, 2, bias=False),
                "behavior": torch.nn.Linear(2, 2, bias=False),
            })

    model = TwoAdapters()
    for name, value in model.named_parameters():
        value.requires_grad_(".policy." in name)
    names = assert_only_adapter_trainable(model, "policy")
    assert names and all(".policy." in name for name in names)

    next(value for name, value in model.named_parameters() if ".behavior." in name).requires_grad_(True)
    with pytest.raises(RealRunnerError, match="non-selected"):
        assert_only_adapter_trainable(model, "policy")


def test_behavior_adapter_reload_name_is_bound_to_receipt_identity() -> None:
    identity = {
        "policy_version": "shared-wave-001-input",
        "behavior_version": "shared-wave-001-input",
        "behavior_sha256": "b" * 64,
    }
    assert (
        require_behavior_adapter_name("shared-wave-001-input", identity)
        == "shared-wave-001-input"
    )
    with pytest.raises(RealRunnerError, match="state hash binds PEFT parameter names"):
        require_behavior_adapter_name("online_v2_behavior_w001", identity)


class _CheckpointModel:
    def set_adapter(self, name: str) -> None:
        self.adapter = name

    def save_pretrained(self, path: Path, **_: object) -> None:
        path.mkdir(parents=True)
        (path / "adapter_model.safetensors").write_bytes(b"adapter")
        (path / "adapter_config.json").write_text("{}", encoding="utf-8")


def test_real_checkpoint_publishes_cross_wave_training_state(
    tmp_path: Path,
) -> None:
    output = tmp_path / "wave1"
    evidence = {"wave_id": "wave-0001", "next_policy_version": "policy-0002"}
    result = _write_checkpoint_outputs(
        output, _CheckpointModel(), "policy", torch.nn.Linear(2, 2),
        {"schema_version": "test", "optimizer": {"state": {}}},
        {"accepted": True}, evidence,
    )
    checkpoint = Path(result["path"])
    manifest = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "fate.online_v2.real_checkpoint.v2"
    assert "training_state.pt" in {record["path"] for record in manifest["files"]}
    assert result["resume_training_state"] == {
        "path": str((checkpoint / "training_state.pt").resolve()),
        "sha256": sha256_file(checkpoint / "training_state.pt"),
    }

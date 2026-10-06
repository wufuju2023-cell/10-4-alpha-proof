import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

torch = pytest.importorskip("torch")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ce_arm.train as train_module
from ce_arm.train import (encode_rows, load_transition_rows, reconcile_checkpoints,
                          sha256_file, validate_train_config, verify_asset_lock,
                          validate_replay_bundle, verify_checkpoint, verify_initial_adapter,
                          write_checkpoint, _seed_all, _select_replay_batch)


PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY_HEX = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
).hex()
VERIFIER_LOCK_SHA = "9" * 64
VERIFIER_ID = "lean428"


class FakeTokenizer:
    pad_token_id = 0
    eos_token_id = 2
    vocab_size = 256

    def encode(self, text, add_special_tokens):
        return ([1] if add_special_tokens else []) + list(text.encode("ascii"))

    def decode(self, ids, **kwargs):
        return bytes(ids).decode("ascii")


class ContextualBoundaryTokenizer(FakeTokenizer):
    def encode(self, text, add_special_tokens):
        if text == "c" and not add_special_tokens:
            return [ord("x")]
        return super().encode(text, add_special_tokens)

    def decode(self, ids, **kwargs):
        if ids == [250]:
            return "c"
        return super().decode(ids, **kwargs)


def _row(value_target=-1.0, *, path_index=0, path_length=1,
         transition_id=None):
    tokenizer = FakeTokenizer()
    prompt, action = "ab", "c"
    before = f"{path_index + 1:x}" * 64
    after = f"{path_index + 2:x}" * 64
    action_ids = tokenizer.encode(action, False)
    if transition_id is None:
        transition_id = train_module.object_hash({
            "request_sha256": "d" * 64,
            "step_index": path_index,
            "before": before,
            "action_token_ids": action_ids,
            "after": after,
        })
    row = {
        "prompt": prompt, "action": action, "kind": "proof", "solved": True,
        "terminal_verified": True, "value_target": value_target,
        "extra": {
            "transition_id": transition_id, "wave_index": 1,
            "receipt_id": "receipt-1", "request_sha256": "d" * 64,
            "path_index": path_index, "path_length": path_length,
            "actor_config_sha256": "a" * 64, "budget_config_sha256": "b" * 64,
            "tokenizer_lock_sha256": "c" * 64,
            "prompt_token_ids": tokenizer.encode(prompt, True),
            "action_token_ids": action_ids,
            "state_before_sha256": before,
            "state_after_sha256": after,
        },
    }
    row["extra"]["selected_path_step"] = {
        "step_index": path_index,
        "state_before_sha256": before,
        "state_after_sha256": after,
        "prompt": prompt,
        "prompt_token_ids": tokenizer.encode(prompt, True),
        "raw_completion_token_ids": action_ids,
        "tactic_token_span": [0, len(action_ids)],
        "action": action,
        "value_target": value_target,
    }
    _seal_rows([row])
    return row


def _seal_rows(rows, *, verifier_lock_sha=VERIFIER_LOCK_SHA, private_key=PRIVATE_KEY):
    selected_path = [
        row["extra"]["selected_path_step"]
        for row in sorted(rows, key=lambda item: item["extra"]["path_index"])
    ]
    verification = {
        "verifier_id": VERIFIER_ID,
        "verifier_lock_sha256": verifier_lock_sha,
        "request_sha256": "d" * 64,
        "selected_path_sha256": train_module.object_hash(selected_path),
        "initial_state_sha256": selected_path[0]["state_before_sha256"],
        "final_state_sha256": selected_path[-1]["state_after_sha256"],
        "actor_config_sha256": "a" * 64,
        "budget_config_sha256": "b" * 64,
        "tokenizer_lock_sha256": "c" * 64,
        "result": "verified",
        "kernel_exit_code": 0,
    }
    verification["verification_receipt_sha256"] = train_module.object_hash(verification)
    verification["signature_hex"] = private_key.sign(bytes.fromhex(
        verification["verification_receipt_sha256"]
    )).hex()
    for row in rows:
        row["extra"]["selected_path_sha256"] = verification["selected_path_sha256"]
        row["extra"]["verification"] = verification
        row["extra"]["verifier_receipt_sha256"] = verification[
            "verification_receipt_sha256"
        ]
    return rows


def test_prompt_is_masked_and_action_is_supervised():
    batch = encode_rows(FakeTokenizer(), [_row()], max_length=8, device="cpu")
    assert batch["labels"].tolist() == [[-100, -100, -100, ord("c")]]
    assert batch["prompt_lengths"].tolist() == [3]


def test_contextual_action_ids_are_supervised_without_standalone_reencoding():
    row = _row()
    row["extra"]["action_token_ids"] = [250]
    batch = encode_rows(ContextualBoundaryTokenizer(), [row], max_length=8, device="cpu")
    assert batch["labels"].tolist() == [[-100, -100, -100, 250]]


def test_wrong_stored_token_ids_are_rejected():
    row = _row()
    row["extra"]["action_token_ids"] = [ord("x")]
    with pytest.raises(ValueError, match="action_token_ids"):
        encode_rows(FakeTokenizer(), [row], max_length=8, device="cpu")


def test_overlength_is_not_silently_truncated():
    with pytest.raises(ValueError, match="truncation is forbidden"):
        encode_rows(FakeTokenizer(), [_row()], max_length=3, device="cpu")


def test_transition_target_range_is_strict(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(_row(value_target=1.0)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="value_target"):
        _load_rows(path)


def _load_rows(path: Path, *, verifier_lock_sha=VERIFIER_LOCK_SHA,
               public_key_hex=PUBLIC_KEY_HEX):
    return load_transition_rows(
        path, vocabulary_size=256, actor_config_sha256="a" * 64,
        budget_config_sha256="b" * 64, tokenizer_lock_sha256="c" * 64,
        max_wave=1,
        trusted_verifier_id=VERIFIER_ID,
        trusted_verifier_lock_sha256=verifier_lock_sha,
        trusted_verifier_public_key_hex=public_key_hex,
    )


def test_trainer_rederives_one_step_target_and_rejects_minus_64(tmp_path):
    path = tmp_path / "forged-one-step.jsonl"
    path.write_text(json.dumps(_row(value_target=-64.0)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="value_target mismatch"):
        _load_rows(path)


@pytest.mark.parametrize("missing", ["path_index", "path_length"])
def test_trainer_rejects_missing_path_provenance(tmp_path, missing):
    row = _row()
    del row["extra"][missing]
    path = tmp_path / f"missing-{missing}.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="path_index/path_length"):
        _load_rows(path)


def test_trainer_requires_every_row_of_claimed_verified_path(tmp_path):
    # Rehashing a one-row JSONL/manifest cannot turn it into a valid 64-step
    # path: the trainer requires the complete 0..L-1 index set per receipt.
    row = _row(value_target=-2.0, path_index=0, path_length=2)
    path = tmp_path / "incomplete-path.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete/noncontiguous"):
        _load_rows(path)


def test_trainer_accepts_complete_multistep_derived_targets(tmp_path):
    first = _row(value_target=-2.0, path_index=0, path_length=2)
    second = _row(value_target=-1.0, path_index=1, path_length=2)
    _seal_rows([first, second])
    path = tmp_path / "complete-path.jsonl"
    path.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    assert [row["value_target"] for row in _load_rows(path)] == [-2.0, -1.0]


def test_trainer_rejects_inconsistent_path_length_within_receipt(tmp_path):
    first = _row(value_target=-2.0, path_index=0, path_length=2)
    second = _row(value_target=-2.0, path_index=1, path_length=3)
    _seal_rows([first, second])
    path = tmp_path / "inconsistent-length.jsonl"
    path.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="inconsistent path_length"):
        _load_rows(path)


def test_manifest_hash_blocks_direct_jsonl_tamper_and_semantics_block_rehash(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    transitions = bundle / "transitions.jsonl"
    verifier_lock = tmp_path / "verifier.json"
    verifier_lock.write_text(
        json.dumps({"ed25519_public_key_hex": PUBLIC_KEY_HEX}), encoding="utf-8"
    )
    verifier_lock_sha = sha256_file(verifier_lock)
    first = _row(value_target=-2.0, path_index=0, path_length=2)
    second = _row(value_target=-1.0, path_index=1, path_length=2)
    _seal_rows([first, second], verifier_lock_sha=verifier_lock_sha)
    transitions.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    budget_lock = tmp_path / "budget.json"
    representative_plan = (
        ROOT.parents[1] / "workstreams" / "policy_service_bridge" / "config"
        / "representative_smoke_plan.frozen.json"
    )
    budget = json.loads(representative_plan.read_text(encoding="utf-8"))
    budget_lock.write_text(json.dumps(budget), encoding="utf-8")
    budget_limits = {
        "max_attempts_per_problem": 1,
        "max_generated_tokens_per_problem": 64 * 256,
        "max_lean_tactic_executions_per_problem": 64,
    }
    config_sha = "f" * 64
    config = {"locks": {
        "trusted_verifier_lock": {
            "path": str(verifier_lock), "verifier_id": "lean428",
            "sha256": verifier_lock_sha,
        },
        "shared_actor_config": {"sha256": "8" * 64,
                                "receipt_identity_sha256": "a" * 64},
        "shared_budget_config": {"path": str(budget_lock), "sha256": "b" * 64},
        "tokenizer_lock": {"sha256": "7" * 64,
                           "receipt_identity_sha256": "c" * 64},
        "course_manifest": {"sha256": "e" * 64},
    }}
    costs = {"p0": {"attempts": 1, "generated_tokens": 1,
                      "lean_tactic_executions": 1}}
    manifest = {
        "schema_version": 2, "status": "complete", "max_wave": 1,
        "ce_config_sha256": config_sha,
        "actor_config_sha256": "a" * 64, "budget_config_sha256": "b" * 64,
        "tokenizer_lock_sha256": "c" * 64,
        "trusted_verifier_id": "lean428",
        "trusted_verifier_lock_sha256": verifier_lock_sha,
        "trusted_verifier_public_key_sha256": train_module.hashlib.sha256(
            bytes.fromhex(PUBLIC_KEY_HEX)
        ).hexdigest(),
        "course_manifest_sha256": "e" * 64,
        "value_bins": 64, "distance_overflow_policy": "reject",
        "budget_limits": budget_limits, "per_problem_costs": costs,
        "per_problem_costs_sha256": train_module.object_hash(costs),
        "stats": {"receipts": 1, "generated_tokens": 1,
                  "lean_tactic_executions": 1},
        "transition_sha256": sha256_file(transitions),
    }
    manifest["manifest_payload_sha256"] = train_module.object_hash(manifest)
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    validate_replay_bundle(bundle, config, 1, config_sha)
    assert len(_load_rows(transitions, verifier_lock_sha=verifier_lock_sha)) == 2

    # Exact joint-tamper repro: delete the terminal row, shrink L=2 to L=1,
    # change -2 to -1, and later recompute every attacker-controlled self-hash.
    # The original verifier signature/path commitment is deliberately retained;
    # an attacker does not possess the verifier private key.
    forged = json.loads(json.dumps(first))
    forged["value_target"] = -1.0
    forged["extra"]["path_length"] = 1
    forged["extra"]["selected_path_step"]["value_target"] = -1.0
    transitions.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="transition file hash mismatch"):
        validate_replay_bundle(bundle, config, 1, config_sha)

    # Even if an attacker can rewrite the manifest's self-hashes, semantic
    # re-derivation from the complete path provenance still rejects the row.
    manifest["transition_sha256"] = sha256_file(transitions)
    manifest["manifest_payload_sha256"] = train_module.object_hash(
        manifest, "manifest_payload_sha256"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    validated_path, _, _ = validate_replay_bundle(bundle, config, 1, config_sha)
    with pytest.raises(ValueError, match="selected path hash mismatch|verifier commitment"):
        _load_rows(validated_path, verifier_lock_sha=verifier_lock_sha)

    # Stronger attack: replace the lock file with an attacker key, sign the
    # collapsed path using that key while claiming the original frozen lock
    # hash, and recompute every replay self-hash.  The training-side lock loader
    # must reject the mutable file before its key is consumed.
    attacker_key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    attacker_public = attacker_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    _seal_rows(
        [forged], verifier_lock_sha=verifier_lock_sha, private_key=attacker_key
    )
    transitions.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    verifier_lock.write_text(
        json.dumps({"ed25519_public_key_hex": attacker_public}), encoding="utf-8"
    )
    manifest["trusted_verifier_public_key_sha256"] = train_module.hashlib.sha256(
        bytes.fromhex(attacker_public)
    ).hexdigest()
    manifest["transition_sha256"] = sha256_file(transitions)
    manifest["manifest_payload_sha256"] = train_module.object_hash(
        manifest, "manifest_payload_sha256"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="lock file hash"):
        validate_replay_bundle(bundle, config, 1, config_sha)


def test_config_rejects_nonsensical_numerics():
    config = {
        "status": "frozen", "model": {"value_bins": 64},
        "replay": {
            "distance_overflow_policy": "reject",
            "sampling": "uniform_over_transitions",
        },
        "train": {
            "batch_size": 2, "micro_batch_size": 1, "steps_per_wave": 1,
            "max_length": 10, "policy_lr": 1e-4, "value_lr": 1e-4,
            "value_coef": 1e-3, "max_grad_norm": 1.0, "seed": 1,
            "checkpoint_every_steps": 1,
            "lora": {"r": 2, "alpha": 4, "dropout": 1.2, "target_modules": ["q_proj"]},
        },
    }
    with pytest.raises(ValueError, match="dropout"):
        validate_train_config(config)


def test_full_replay_selects_every_validated_transition_exactly_once():
    rows = [{"id": index} for index in range(20)]
    manifest = {"stats": {"transitions": 20}}
    selected = _select_replay_batch(
        rows, manifest, count=20, sampling="full_replay", rng=random.Random(7)
    )
    assert selected == rows
    assert len({id(row) for row in selected}) == 20


@pytest.mark.parametrize(
    ("manifest_count", "batch_size", "match"),
    [(19, 20, "manifest transition count"), (20, 1, "batch_size")],
)
def test_full_replay_fails_closed_on_any_effective_batch_shrink(
    manifest_count, batch_size, match,
):
    rows = [{"id": index} for index in range(20)]
    manifest = {"stats": {"transitions": manifest_count}}
    with pytest.raises(ValueError, match=match):
        _select_replay_batch(
            rows, manifest, count=batch_size, sampling="full_replay",
            rng=random.Random(7),
        )


class FakeModel:
    def save_pretrained(self, path):
        path = Path(path)
        path.mkdir(parents=True)
        (path / "adapter.bin").write_bytes(b"adapter")


def test_atomic_checkpoint_manifest_detects_corruption(tmp_path):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    checkpoint = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={"config_sha256": "a" * 64},
        receipt={"selection_sha256": "b" * 64},
    )
    assert verify_checkpoint(checkpoint)["status"] == "complete"
    (checkpoint / "value_head.pt").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch|size mismatch"):
        verify_checkpoint(checkpoint)


def test_checkpoint_manifest_rejects_mixed_extra_component(tmp_path):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    checkpoint = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={}, receipt={},
    )
    (checkpoint / "foreign.bin").write_bytes(b"mixed")
    with pytest.raises(ValueError, match="file set"):
        verify_checkpoint(checkpoint)


def test_checkpoint_orphan_after_directory_rename_is_adopted(tmp_path, monkeypatch):
    """Probe the exact crash window: complete dir exists but latest.json does not."""
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    real_atomic_json = train_module._atomic_json

    def crash_before_latest(path, value):
        if Path(path).name == "latest.json":
            raise RuntimeError("injected crash after checkpoint rename")
        return real_atomic_json(path, value)

    monkeypatch.setattr(train_module, "_atomic_json", crash_before_latest)
    with pytest.raises(RuntimeError, match="injected crash"):
        write_checkpoint(
            FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
            global_step=1, rng=random.Random(1), metadata={}, receipt={},
        )
    orphan = next(
        item for item in (tmp_path / "checkpoints").iterdir()
        if item.is_dir() and not item.name.startswith(".")
    )
    assert verify_checkpoint(orphan)["status"] == "complete"
    assert not (tmp_path / "latest.json").exists()

    monkeypatch.setattr(train_module, "_atomic_json", real_atomic_json)
    recovered = reconcile_checkpoints(tmp_path)
    assert recovered == orphan.resolve()
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["checkpoint_manifest_sha256"] == sha256_file(
        orphan / "checkpoint_manifest.json"
    )


def test_checkpoint_retry_is_idempotent_after_latest_publication_crash(tmp_path, monkeypatch):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    real_atomic_json = train_module._atomic_json

    def crash_before_latest(path, value):
        if Path(path).name == "latest.json":
            raise RuntimeError("injected crash after checkpoint rename")
        return real_atomic_json(path, value)

    monkeypatch.setattr(train_module, "_atomic_json", crash_before_latest)
    with pytest.raises(RuntimeError, match="injected crash"):
        write_checkpoint(
            FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
            global_step=1, rng=random.Random(1), metadata={}, receipt={},
        )
    monkeypatch.setattr(train_module, "_atomic_json", real_atomic_json)
    recovered = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(999), metadata={"ignored": True}, receipt={},
    )
    assert recovered.is_dir()
    assert (tmp_path / "latest.json").is_file()


def test_partial_checkpoint_before_directory_rename_is_cleaned_and_retryable(
        tmp_path, monkeypatch):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    real_replace = train_module.os.replace

    def crash_before_directory_rename(source, destination):
        destination = Path(destination)
        if destination.parent.name == "checkpoints" and destination.name.startswith("wave_"):
            raise RuntimeError("injected crash before checkpoint rename")
        return real_replace(source, destination)

    monkeypatch.setattr(train_module.os, "replace", crash_before_directory_rename)
    with pytest.raises(RuntimeError, match="injected crash"):
        write_checkpoint(
            FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
            global_step=1, rng=random.Random(1), metadata={}, receipt={},
        )
    monkeypatch.setattr(train_module.os, "replace", real_replace)
    assert reconcile_checkpoints(tmp_path) is None
    checkpoint = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={}, receipt={},
    )
    assert checkpoint.is_dir()


def test_stale_latest_advances_to_unique_manifest_bound_successor(tmp_path):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    first = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={}, receipt={},
    )
    first_hash = sha256_file(first / "checkpoint_manifest.json")
    stale_latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    second = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=2,
        global_step=2, rng=random.Random(1), metadata={}, receipt={},
        parent_checkpoint_manifest_sha256=first_hash,
    )
    # Simulate the durable state after child rename but before its latest write.
    train_module._atomic_json(tmp_path / "latest.json", stale_latest)
    recovered = reconcile_checkpoints(tmp_path, requested_resume=first)
    assert recovered == second.resolve()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))[
        "checkpoint"
    ] == str(second.resolve())


def test_checkpoint_publish_rejects_sequential_stale_parent_before_saving(tmp_path):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    first = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={}, receipt={},
    )
    first_hash = sha256_file(first / "checkpoint_manifest.json")
    second = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=2,
        global_step=2, rng=random.Random(1), metadata={}, receipt={},
        parent_checkpoint_manifest_sha256=first_hash,
    )
    second_hash = sha256_file(second / "checkpoint_manifest.json")

    class MustNotSave(FakeModel):
        def save_pretrained(self, path):
            raise AssertionError("stale writer reached expensive checkpoint serialization")

    with pytest.raises(ValueError, match="stale checkpoint parent"):
        write_checkpoint(
            MustNotSave(), head, optimizer, tmp_path, wave_index=1, wave_step=3,
            global_step=3, rng=random.Random(1), metadata={}, receipt={},
            parent_checkpoint_manifest_sha256=first_hash,
        )
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["checkpoint_manifest_sha256"] == second_hash
    assert not (tmp_path / "checkpoints" /
                "wave_001_step_000003_global_00000003").exists()


def test_concurrent_same_parent_cannot_publish_two_successors(tmp_path):
    head = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(head.parameters())
    first = write_checkpoint(
        FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=1,
        global_step=1, rng=random.Random(1), metadata={}, receipt={},
    )
    first_hash = sha256_file(first / "checkpoint_manifest.json")
    serialization_started = threading.Event()
    allow_finish = threading.Event()

    class SlowModel(FakeModel):
        def save_pretrained(self, path):
            serialization_started.set()
            if not allow_finish.wait(timeout=5):
                raise TimeoutError("test did not release checkpoint serialization")
            super().save_pretrained(path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        winner = pool.submit(
            write_checkpoint, SlowModel(), head, optimizer, tmp_path,
            wave_index=1, wave_step=2, global_step=2, rng=random.Random(2),
            metadata={}, receipt={}, parent_checkpoint_manifest_sha256=first_hash,
        )
        assert serialization_started.wait(timeout=5)
        try:
            with pytest.raises(RuntimeError, match="another process is publishing"):
                write_checkpoint(
                    FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=3,
                    global_step=3, rng=random.Random(3), metadata={}, receipt={},
                    parent_checkpoint_manifest_sha256=first_hash,
                )
        finally:
            allow_finish.set()
        published = winner.result(timeout=5)

    assert published.is_dir()
    # A blocked writer that retries after the winner commits observes a stale
    # compare value and is rejected before it can create a sibling branch.
    with pytest.raises(ValueError, match="stale checkpoint parent"):
        write_checkpoint(
            FakeModel(), head, optimizer, tmp_path, wave_index=1, wave_step=3,
            global_step=3, rng=random.Random(3), metadata={}, receipt={},
            parent_checkpoint_manifest_sha256=first_hash,
        )
    complete = [item for item in (tmp_path / "checkpoints").iterdir()
                if item.is_dir() and not item.name.startswith(".")]
    assert len(complete) == 2


def test_torch_and_sampler_seed_are_reproducible():
    rng1, _ = _seed_all(7, torch, False)
    first = (rng1.random(), torch.rand(3))
    rng2, _ = _seed_all(7, torch, False)
    second = (rng2.random(), torch.rand(3))
    assert first[0] == second[0]
    assert torch.equal(first[1], second[1])


def test_asset_lock_rejects_value_head_hash_change(tmp_path, monkeypatch):
    roles = ["model_config", "generation_config", "tokenizer_config", "tokenizer",
             "model_index", "value_head", "target_value_head_source", "model_shard",
             "initial_adapter_config", "initial_adapter_model",
             "initial_adapter_manifest"]
    files = []
    model_root = tmp_path / "model"
    model_root.mkdir()
    adapter_root = tmp_path / "adapter"
    adapter_root.mkdir()
    adapter_names = {
        "initial_adapter_config": "adapter_config.json",
        "initial_adapter_model": "adapter_model.safetensors",
        "initial_adapter_manifest": "formal_initial_adapter_manifest.json",
    }
    for role in roles:
        if role in adapter_names:
            path = adapter_root / adapter_names[role]
        else:
            path = ((tmp_path if role in {"value_head", "target_value_head_source"}
                     else model_root) / f"{role}.bin")
        if role == "model_index":
            path.write_text(json.dumps({"weight_map": {"x": "model_shard.bin"}}), encoding="utf-8")
        else:
            path.write_bytes(role.encode())
        import hashlib
        files.append({"role": role, "path": str(path), "size": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = {"torch": torch.__version__, "transformers": "x", "peft": "y",
               "cryptography": "z"}
    lock = {
        "schema_version": 1,
        "model_root": str(model_root),
        "model_revision": "1" * 40,
        "initial_adapter_root": str(adapter_root),
        "files": files,
        "target_git": {"path": str(repo), "commit": "abc"},
        "runtime_versions": runtime,
    }
    lock_path = tmp_path / "asset-lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    import hashlib
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    def fake_git(command, cwd, text):
        return "abc\n" if "rev-parse" in command else ""

    monkeypatch.setattr("subprocess.check_output", fake_git)
    verify_asset_lock(lock_path, lock_hash, runtime_versions=runtime)
    (tmp_path / "value_head.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        verify_asset_lock(lock_path, lock_hash, runtime_versions=runtime)


def test_initial_adapter_pin_is_verified_before_load(tmp_path):
    import hashlib

    root = tmp_path / "adapter"
    root.mkdir()
    adapter_config = root / "adapter_config.json"
    adapter_model = root / "adapter_model.safetensors"
    adapter_config.write_text("{}", encoding="utf-8")
    adapter_model.write_bytes(b"frozen-adapter")
    files = {
        path.name: {"bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (adapter_config, adapter_model)
    }
    manifest = {
        "schema_version": "fate-m.formal-initial-lora.v1",
        "trained": False,
        "base_model_path": str(tmp_path / "model"),
        "base_model_revision": "1" * 40,
        "lora": {"r": 16, "alpha": 32, "dropout": 0.02,
                 "target_modules": ["q_proj"]},
        "trainable_state_sha256": "2" * 64,
        "files": files,
    }
    manifest["manifest_payload_sha256"] = train_module.object_hash(manifest)
    manifest_path = root / "formal_initial_adapter_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = {
        "model": {
            "base_path": str(tmp_path / "model"),
            "base_revision": "1" * 40,
            "initial_adapter": {
                "path": str(root),
                "manifest_payload_sha256": manifest["manifest_payload_sha256"],
                "adapter_model_sha256": files[adapter_model.name]["sha256"],
                "adapter_config_sha256": files[adapter_config.name]["sha256"],
                "trainable_state_sha256": "2" * 64,
            },
        },
        "train": {"lora": {"r": 16, "alpha": 32, "dropout": 0.02,
                            "target_modules": ["q_proj"]}},
    }
    resolved, observed = verify_initial_adapter(
        config, {"initial_adapter_root": str(root)}
    )
    assert resolved == root.resolve()
    assert observed["manifest_payload_sha256"] == manifest["manifest_payload_sha256"]
    adapter_model.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="adapter_model_sha256 mismatch"):
        verify_initial_adapter(config, {"initial_adapter_root": str(root)})

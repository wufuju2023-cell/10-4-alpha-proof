import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ce_arm.course import prepare_course
from ce_arm.cli import build_replay_from_config
from ce_arm.locks import generate_tokenizer_lock
from ce_arm.replay import (ReplayPolicy, build_replay,
                           canonical_receipt_to_transitions, object_hash,
                           sha256_file)


H_ACTOR = "a" * 64
H_BUDGET = "b" * 64
H_TOKENIZER = "c" * 64
H_VERIFIER = "d" * 64
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY_HEX = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
).hex()


class FakeTokenizer:
    vocab_size = 256
    pad_token_id = 0
    eos_token_id = 2

    def encode(self, text, add_special_tokens):
        return ([1] if add_special_tokens else []) + list(text.encode("ascii"))

    def decode(self, ids, **kwargs):
        return bytes(ids).decode("ascii")


class ContextualBoundaryTokenizer(FakeTokenizer):
    """Models a BPE token that is valid only in the prompt/completion context."""

    def encode(self, text, add_special_tokens):
        if text == "trivial" and not add_special_tokens:
            return [ord(char) for char in "standalone"]
        return super().encode(text, add_special_tokens)

    def decode(self, ids, **kwargs):
        if ids == [250]:
            return "trivial"
        return super().decode(ids, **kwargs)


def _make_course(path: Path):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for family in range(1, 21):
            for variant in range(1, 201):
                statement = f"theorem f{family}_v{variant} : True := by\n  sorry\n"
                row = {
                    "id": f"f{family}_v{variant}",
                    "family_index": family,
                    "variant_index": variant,
                    "formal_statement": statement,
                    "sha256": hashlib.sha256(statement.encode()).hexdigest(),
                }
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")


@pytest.fixture()
def frozen_course(tmp_path):
    source = tmp_path / "problems.jsonl"
    _make_course(source)
    derived = tmp_path / "derived"
    prepare_course(source, derived)
    manifest = derived / "manifest.json"
    index = {row["id"]: row for row in map(json.loads, (derived / "course_index.jsonl").read_text().splitlines())}
    policy = ReplayPolicy(
        course_manifest_path=manifest,
        course_manifest_sha256=sha256_file(manifest),
        ce_config_sha256="e" * 64,
        max_wave=10,
        actor_config_sha256=H_ACTOR,
        budget_config_sha256=H_BUDGET,
        tokenizer_lock_sha256=H_TOKENIZER,
        trusted_verifier_id="lean428-kernel",
        trusted_verifier_lock_sha256=H_VERIFIER,
        trusted_verifier_public_key_hex=PUBLIC_KEY_HEX,
        max_attempts_per_problem=2,
        max_generated_tokens_per_problem=256,
        max_lean_tactic_executions_per_problem=64,
    )
    return tmp_path, index, policy


def _receipt(index, problem_id="f1_v1", *, action="trivial"):
    row = index[problem_id]
    before, after = "1" * 64, "2" * 64
    tokenizer = FakeTokenizer()
    prompt = "prove true"
    action_ids = tokenizer.encode(action, add_special_tokens=False)
    request = {
        "problem_id": problem_id,
        "statement_sha256": row["statement_sha256"],
        "wave_index": row["variant_index"],
        "actor_config_sha256": H_ACTOR,
        "budget_config_sha256": H_BUDGET,
        "initial_state_sha256": before,
    }
    path = [{
        "step_index": 0,
        "state_before_sha256": before,
        "state_after_sha256": after,
        "prompt": prompt,
        "prompt_token_ids": tokenizer.encode(prompt, add_special_tokens=True),
        "raw_completion_token_ids": [120] + action_ids + [121],
        "tactic_token_span": [1, 1 + len(action_ids)],
        "action": action,
        "value_target": -1.0,
    }]
    request_hash = object_hash(request)
    verification = {
        "verifier_id": "lean428-kernel",
        "verifier_lock_sha256": H_VERIFIER,
        "request_sha256": request_hash,
        "statement_sha256": row["statement_sha256"],
        "selected_path_sha256": object_hash(path),
        "initial_state_sha256": before,
        "final_state_sha256": after,
        "result": "verified",
        "kernel_exit_code": 0,
    }
    verification["verification_receipt_sha256"] = object_hash(verification)
    verification["signature_hex"] = PRIVATE_KEY.sign(
        bytes.fromhex(verification["verification_receipt_sha256"])
    ).hex()
    receipt = {
        "schema_version": 2,
        "receipt_id": "receipt-1",
        "attempt_id": "attempt-1",
        "problem_id": problem_id,
        "statement_sha256": row["statement_sha256"],
        "outcome": "proof",
        "actor_config_sha256": H_ACTOR,
        "budget_config_sha256": H_BUDGET,
        "tokenizer_lock_sha256": H_TOKENIZER,
        "request": request,
        "request_sha256": request_hash,
        "selected_path": path,
        "verification": verification,
        "cost": {"generated_tokens": len(action_ids), "lean_tactic_executions": 1},
    }
    _rehash(receipt)
    return receipt


def _rehash(receipt):
    receipt["verification"]["selected_path_sha256"] = object_hash(receipt["selected_path"])
    receipt["verification"].update({
        "actor_config_sha256": receipt["actor_config_sha256"],
        "budget_config_sha256": receipt["budget_config_sha256"],
        "tokenizer_lock_sha256": receipt["tokenizer_lock_sha256"],
        "cost_sha256": object_hash(receipt["cost"]),
        "actor_envelope_sha256": object_hash(
            {key: value for key, value in receipt.items()
             if key not in {"verification", "receipt_sha256"}}
        ),
    })
    payload = {key: value for key, value in receipt["verification"].items()
               if key not in {"verification_receipt_sha256", "signature_hex"}}
    receipt["verification"]["verification_receipt_sha256"] = object_hash(payload)
    receipt["verification"]["signature_hex"] = PRIVATE_KEY.sign(
        bytes.fromhex(receipt["verification"]["verification_receipt_sha256"])
    ).hex()
    receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")


def _set_linear_path(receipt, actions):
    """Replace the fixture path with a canonical root-to-terminal tactic path."""
    tokenizer = FakeTokenizer()
    initial = receipt["request"]["initial_state_sha256"]
    states = [initial] + [
        hashlib.sha256(f"state-{index}".encode()).hexdigest()
        for index in range(1, len(actions) + 1)
    ]
    path = []
    for index, action in enumerate(actions):
        prompt = f"state {index}"
        action_ids = tokenizer.encode(action, add_special_tokens=False)
        path.append({
            "step_index": index,
            "state_before_sha256": states[index],
            "state_after_sha256": states[index + 1],
            "prompt": prompt,
            "prompt_token_ids": tokenizer.encode(prompt, add_special_tokens=True),
            "raw_completion_token_ids": [120] + action_ids + [121],
            "tactic_token_span": [1, 1 + len(action_ids)],
            "action": action,
            "value_target": -float(len(actions) - index),
        })
    receipt["selected_path"] = path
    receipt["cost"] = {
        "generated_tokens": sum(len(action) for action in actions),
        # Keep a 65-step path within the independent fixture budget so the
        # value-horizon check, rather than the cost guard, is what rejects it.
        "lean_tactic_executions": min(len(actions), 64),
    }
    receipt["verification"]["final_state_sha256"] = states[-1]
    _rehash(receipt)
    return receipt


def test_verified_receipt_binds_request_verifier_tokens_and_transition(frozen_course):
    _, index, policy = frozen_course
    rows = canonical_receipt_to_transitions(
        _receipt(index), policy=policy, course_index=index, tokenizer=FakeTokenizer()
    )
    assert len(rows) == 1
    assert rows[0]["extra"]["action_token_ids"] == list(b"trivial")
    assert len(rows[0]["extra"]["transition_id"]) == 64


def test_contextual_action_ids_are_preserved_when_they_decode_to_executed_tactic(
        frozen_course):
    _, index, policy = frozen_course
    receipt = _receipt(index)
    receipt["selected_path"][0]["raw_completion_token_ids"] = [120, 250, 121]
    receipt["selected_path"][0]["tactic_token_span"] = [1, 2]
    _rehash(receipt)
    rows = canonical_receipt_to_transitions(
        receipt, policy=policy, course_index=index,
        tokenizer=ContextualBoundaryTokenizer(),
    )
    assert rows[0]["extra"]["action_token_ids"] == [250]


def test_value_targets_are_derived_from_correct_multistep_path(frozen_course):
    _, index, policy = frozen_course
    receipt = _set_linear_path(_receipt(index), ["a", "b", "c"])
    rows = canonical_receipt_to_transitions(
        receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
    )
    assert [row["value_target"] for row in rows] == [-3.0, -2.0, -1.0]


def test_one_step_actor_labelled_minus_64_is_rejected(frozen_course):
    _, index, policy = frozen_course
    receipt = _receipt(index)
    receipt["selected_path"][0]["value_target"] = -64.0
    _rehash(receipt)
    with pytest.raises(ValueError, match="value_target mismatch"):
        canonical_receipt_to_transitions(
            receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
        )


@pytest.mark.parametrize("indices", ([1, 0], [0, 2], [0, 0]))
def test_reversed_missing_and_duplicate_path_indices_are_rejected(frozen_course, indices):
    _, index, policy = frozen_course
    receipt = _set_linear_path(_receipt(index), ["a", "b"])
    for step, step_index in zip(receipt["selected_path"], indices):
        step["step_index"] = step_index
    _rehash(receipt)
    with pytest.raises(ValueError, match="malformed/noncontiguous"):
        canonical_receipt_to_transitions(
            receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
        )


def test_65_step_path_is_rejected_not_clamped(frozen_course):
    _, index, policy = frozen_course
    receipt = _set_linear_path(_receipt(index), ["a"] * 65)
    with pytest.raises(ValueError, match="65 tactics"):
        canonical_receipt_to_transitions(
            receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
        )


def test_v200_heldout_is_rejected(frozen_course):
    _, index, policy = frozen_course
    with pytest.raises(ValueError, match="held-out"):
        canonical_receipt_to_transitions(
            _receipt(index, "f1_v200"), policy=policy, course_index=index,
            tokenizer=FakeTokenizer(),
        )


def test_future_wave_is_rejected(frozen_course):
    _, index, policy = frozen_course
    with pytest.raises(ValueError, match="future wave"):
        canonical_receipt_to_transitions(
            _receipt(index, "f1_v11"), policy=policy, course_index=index,
            tokenizer=FakeTokenizer(),
        )


def test_wrong_action_token_ids_are_rejected_even_with_rehashed_receipt(frozen_course):
    _, index, policy = frozen_course
    receipt = _receipt(index)
    receipt["selected_path"][0]["raw_completion_token_ids"][1] = ord("x")
    _rehash(receipt)
    with pytest.raises(ValueError, match="action token IDs disagree"):
        canonical_receipt_to_transitions(
            receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
        )


def test_self_asserted_boolean_cannot_replace_trusted_verifier_receipt(frozen_course):
    _, index, policy = frozen_course
    receipt = _receipt(index)
    receipt["verification"] = {"terminal_verified": True}
    receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
    with pytest.raises(ValueError, match="verifier receipt hash"):
        canonical_receipt_to_transitions(
            receipt, policy=policy, course_index=index, tokenizer=FakeTokenizer()
        )


def test_replay_publish_is_atomic_and_complete(frozen_course):
    tmp_path, index, policy = frozen_course
    receipts = tmp_path / "receipts.jsonl"
    receipts.write_text(json.dumps(_receipt(index)) + "\n", encoding="utf-8")
    destination = tmp_path / "bundle"
    manifest = build_replay(receipts, destination, policy=policy, tokenizer=FakeTokenizer())
    assert manifest["status"] == "complete"
    assert manifest["stats"]["transitions"] == 1
    assert manifest["per_problem_costs"] == {
        "f1_v1": {"attempts": 1, "generated_tokens": 7, "lean_tactic_executions": 1}
    }
    assert manifest["per_problem_costs_sha256"] == object_hash(manifest["per_problem_costs"])
    assert sha256_file(destination / "transitions.jsonl") == manifest["transition_sha256"]


def test_joiner_pretty_printed_single_json_receipt_is_consumed_without_copy(frozen_course):
    tmp_path, index, policy = frozen_course
    receipt = tmp_path / "ce-receipt.json"
    receipt.write_text(json.dumps(_receipt(index), indent=2) + "\n", encoding="utf-8")
    destination = tmp_path / "pretty-bundle"
    manifest = build_replay(receipt, destination, policy=policy, tokenizer=FakeTokenizer())
    assert manifest["source_receipts_path"] == str(receipt.resolve())
    assert manifest["source_receipts_sha256"] == sha256_file(receipt)
    assert manifest["stats"]["receipts"] == 1
    assert manifest["stats"]["transitions"] == 1


def test_config_file_hashes_are_distinct_from_actor_and_tokenizer_receipt_identities(
        frozen_course, monkeypatch):
    tmp_path, index, _ = frozen_course
    actor_path = tmp_path / "actor.json"
    actor = {"schema_version": "fate.actor.test.v1", "generation": {"n": 64}}
    actor_path.write_text(json.dumps(actor, indent=2) + "\n", encoding="utf-8")
    actor_identity = object_hash(actor)
    budget_path = tmp_path / "budget.json"
    representative_plan = (
        ROOT.parents[1] / "workstreams" / "policy_service_bridge" / "config"
        / "representative_smoke_plan.frozen.json"
    )
    budget_path.write_bytes(representative_plan.read_bytes())
    model = tmp_path / "model"
    model.mkdir()
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    tokenizer_lock_path = tmp_path / "tokenizer.lock.json"
    tokenizer_lock = generate_tokenizer_lock(model, "1" * 40, tokenizer_lock_path)
    verifier_path = tmp_path / "verifier.json"
    verifier_path.write_text(json.dumps({
        "verifier_id": "lean428-kernel", "ed25519_public_key_hex": PUBLIC_KEY_HEX,
    }), encoding="utf-8")
    course_manifest = tmp_path / "derived" / "manifest.json"
    unused_asset = tmp_path / "asset.json"
    unused_asset.write_text("{}", encoding="utf-8")
    config = {
        "status": "frozen",
        "model": {"base_path": str(model), "value_bins": 64},
        "replay": {"distance_overflow_policy": "reject"},
        "locks": {
            "asset_lock": {"path": str(unused_asset), "sha256": sha256_file(unused_asset)},
            "course_manifest": {"path": str(course_manifest),
                                "sha256": sha256_file(course_manifest)},
            "shared_actor_config": {
                "path": str(actor_path), "sha256": sha256_file(actor_path),
                "receipt_identity_sha256": actor_identity,
            },
            "shared_budget_config": {"path": str(budget_path),
                                     "sha256": sha256_file(budget_path)},
            "tokenizer_lock": {
                "path": str(tokenizer_lock_path), "sha256": sha256_file(tokenizer_lock_path),
                "receipt_identity_sha256": tokenizer_lock["receipt_identity_sha256"],
            },
            "trusted_verifier_lock": {
                "path": str(verifier_path), "sha256": sha256_file(verifier_path),
                "verifier_id": "lean428-kernel",
            },
        },
    }
    config_path = tmp_path / "ce.frozen.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    receipt = _receipt(index)
    receipt["actor_config_sha256"] = actor_identity
    receipt["request"]["actor_config_sha256"] = actor_identity
    receipt["budget_config_sha256"] = sha256_file(budget_path)
    receipt["request"]["budget_config_sha256"] = sha256_file(budget_path)
    receipt["tokenizer_lock_sha256"] = tokenizer_lock["receipt_identity_sha256"]
    receipt["request_sha256"] = object_hash(receipt["request"])
    receipt["verification"]["request_sha256"] = receipt["request_sha256"]
    receipt["verification"]["verifier_lock_sha256"] = sha256_file(verifier_path)
    _rehash(receipt)
    receipt_path = tmp_path / "ce-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda *args, **kwargs: FakeTokenizer())
    ))
    destination = tmp_path / "domain-separated-replay"
    manifest = build_replay_from_config(
        config_path, receipt_path, destination, 1,
        expected_config_sha256=sha256_file(config_path),
    )
    assert manifest["actor_config_sha256"] == actor_identity
    assert manifest["actor_config_sha256"] != sha256_file(actor_path)
    assert manifest["tokenizer_lock_sha256"] == tokenizer_lock["receipt_identity_sha256"]
    assert manifest["tokenizer_lock_sha256"] != sha256_file(tokenizer_lock_path)
    assert manifest["budget_limits"] == {
        "max_attempts_per_problem": 1,
        "max_generated_tokens_per_problem": 64 * 256,
        "max_lean_tactic_executions_per_problem": 64,
    }


def test_multiple_valid_receipts_share_one_cumulative_problem_budget(frozen_course):
    """Two individually legal, signed attempts may not each spend the full budget."""
    tmp_path, index, policy = frozen_course
    first = _receipt(index, action="a" * 130)
    second = _receipt(index, action="b" * 130)
    second["receipt_id"] = "receipt-2"
    second["attempt_id"] = "attempt-2"
    _rehash(second)
    receipts = tmp_path / "over-budget-receipts.jsonl"
    receipts.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="cumulative generated-token budget exceeded"):
        build_replay(
            receipts, tmp_path / "over-budget-bundle", policy=policy,
            tokenizer=FakeTokenizer(),
        )
    assert not (tmp_path / "over-budget-bundle").exists()


def test_multiple_valid_receipts_share_cumulative_lean_budget(frozen_course):
    tmp_path, index, policy = frozen_course
    receipts_list = [_receipt(index, action="a"), _receipt(index, action="b")]
    for number, receipt in enumerate(receipts_list, 1):
        receipt["receipt_id"] = f"receipt-{number}"
        receipt["attempt_id"] = f"attempt-{number}"
        receipt["cost"]["lean_tactic_executions"] = 40
        _rehash(receipt)
    receipts = tmp_path / "over-lean-budget.jsonl"
    receipts.write_text(
        "".join(json.dumps(item) + "\n" for item in receipts_list), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="cumulative Lean-call budget exceeded"):
        build_replay(
            receipts, tmp_path / "over-lean-bundle", policy=policy,
            tokenizer=FakeTokenizer(),
        )


def test_multiple_valid_receipts_share_cumulative_attempt_budget(frozen_course):
    tmp_path, index, policy = frozen_course
    receipts_list = []
    for number, action in enumerate(("a", "b", "c"), 1):
        receipt = _receipt(index, action=action)
        receipt["receipt_id"] = f"receipt-{number}"
        receipt["attempt_id"] = f"attempt-{number}"
        _rehash(receipt)
        receipts_list.append(receipt)
    receipts = tmp_path / "over-attempt-budget.jsonl"
    receipts.write_text(
        "".join(json.dumps(item) + "\n" for item in receipts_list), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="cumulative attempt budget exceeded"):
        build_replay(
            receipts, tmp_path / "over-attempt-bundle", policy=policy,
            tokenizer=FakeTokenizer(),
        )


def test_distinct_signed_attempts_cannot_publish_duplicate_transitions(frozen_course):
    tmp_path, index, policy = frozen_course
    first = _receipt(index)
    second = _receipt(index)
    second["receipt_id"] = "receipt-2"
    second["attempt_id"] = "attempt-2"
    _rehash(second)
    receipts = tmp_path / "duplicate-paths.jsonl"
    receipts.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    destination = tmp_path / "duplicate-path-bundle"
    with pytest.raises(ValueError, match="duplicate transition_id"):
        build_replay(receipts, destination, policy=policy, tokenizer=FakeTokenizer())
    assert not destination.exists()


def test_failed_second_receipt_does_not_publish_partial_bundle(frozen_course):
    tmp_path, index, policy = frozen_course
    first = _receipt(index)
    second = _receipt(index, "f2_v1")
    second["receipt_id"] = "receipt-2"
    second["attempt_id"] = "attempt-2"
    second["selected_path"][0]["action"] = "wrong"
    _rehash(second)
    receipts = tmp_path / "bad-receipts.jsonl"
    receipts.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    destination = tmp_path / "bad-bundle"
    with pytest.raises(ValueError):
        build_replay(receipts, destination, policy=policy, tokenizer=FakeTokenizer())
    assert not destination.exists()

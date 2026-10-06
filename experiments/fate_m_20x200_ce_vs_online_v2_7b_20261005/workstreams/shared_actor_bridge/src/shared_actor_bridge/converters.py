"""Adapters from the single canonical-v2 envelope into both learner boundaries."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .contract import ActorContractError, canonical_sha256, validate_unsigned_envelope
from .attestation import verify_execution_attestation


def _signed_payload_hash(receipt: Mapping[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})


def _require_signed(receipt: Mapping[str, Any], *, verifier_public_key_hex: str,
                    expected_verifier_id: str, expected_verifier_lock_sha256: str,
                    tokenizer: Any, execution_public_key_hex: str | None = None,
                    expected_execution_attester_id: str | None = None,
                    expected_execution_lock_sha256: str | None = None) -> None:
    if receipt.get("receipt_sha256") != _signed_payload_hash(receipt):
        raise ActorContractError("signed receipt hash mismatch")
    verify_execution_attestation(
        receipt,
        public_key_hex=execution_public_key_hex or verifier_public_key_hex,
        expected_attester_id=expected_execution_attester_id or expected_verifier_id,
        expected_attester_lock_sha256=(expected_execution_lock_sha256
                                      or expected_verifier_lock_sha256),
    )
    statuses = [candidate["verifier_status"] for state in receipt.get("search_states", ())
                for candidate in state.get("candidates", ())]
    if receipt.get("outcome") == "infra_error" or "infrastructure_error" in statuses:
        raise ActorContractError("infrastructure error evidence is no-update and fail-closed")
    verification = receipt.get("verification")
    if receipt.get("outcome") != "proof":
        if verification not in (None, {}):
            raise ActorContractError("non-proof search receipt must not claim strict proof replay")
        envelope = deepcopy(dict(receipt)); envelope["receipt_sha256"] = canonical_sha256(
            {key: value for key, value in envelope.items() if key != "receipt_sha256"}
        )
        validate_unsigned_envelope(envelope, tokenizer=tokenizer, permit_execution_attestation=True)
        return
    if not isinstance(verification, Mapping) or verification.get("result") != "verified":
        raise ActorContractError("proof conversion requires a strict-replay signed receipt")
    expected_bindings = {
        "request_sha256": receipt.get("request_sha256"),
        "statement_sha256": receipt.get("statement_sha256"),
        "selected_path_sha256": canonical_sha256(receipt.get("selected_path")),
        "actor_config_sha256": receipt.get("actor_config_sha256"),
        "budget_config_sha256": receipt.get("budget_config_sha256"),
        "tokenizer_lock_sha256": receipt.get("tokenizer_lock_sha256"),
        "cost_sha256": canonical_sha256(receipt.get("cost")),
        "initial_state_sha256": receipt.get("request", {}).get("initial_state_sha256"),
        "final_state_sha256": receipt.get("selected_path", [{}])[-1].get("state_after_sha256"),
        "result": "verified",
        "kernel_exit_code": 0,
    }
    for field, expected in expected_bindings.items():
        if verification.get(field) != expected:
            raise ActorContractError(f"strict verifier {field} binding mismatch")
    if verification.get("verifier_id") != expected_verifier_id:
        raise ActorContractError("strict verifier_id differs from the frozen verifier lock")
    if verification.get("verifier_lock_sha256") != expected_verifier_lock_sha256:
        raise ActorContractError("strict verifier lock hash differs from the frozen lock")
    for field in ("verifier_lock_sha256", "kernel_receipt_sha256"):
        value = verification.get(field)
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ActorContractError(f"strict verifier {field} is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", verifier_public_key_hex):
        raise ActorContractError("trusted verifier public key must be 32-byte lowercase hex")
    verification_payload = {
        key: value for key, value in verification.items()
        if key not in {"verification_receipt_sha256", "signature_hex"}
    }
    verification_hash = canonical_sha256(verification_payload)
    if verification.get("verification_receipt_sha256") != verification_hash:
        raise ActorContractError("verification receipt hash mismatch")
    signature = verification.get("signature_hex")
    if type(signature) is not str or not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ActorContractError("verification lacks a valid Ed25519 signature encoding")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(verifier_public_key_hex))
        key.verify(bytes.fromhex(signature), bytes.fromhex(verification_hash))
    except (ImportError, ValueError, InvalidSignature) as exc:
        raise ActorContractError("strict verifier Ed25519 signature check failed") from exc
    envelope = {key: deepcopy(value) for key, value in receipt.items()}
    envelope["verification"] = None
    envelope["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in envelope.items() if key != "receipt_sha256"}
    )
    validate_unsigned_envelope(envelope, tokenizer=tokenizer, permit_execution_attestation=True)
    expected_actor_hash = canonical_sha256({
        key: value for key, value in envelope.items() if key not in {"verification", "receipt_sha256"}
    })
    if verification.get("actor_envelope_sha256") != expected_actor_hash:
        raise ActorContractError("strict verifier signature is not bound to this actor envelope")


def to_ce_receipt(signed_receipt: Mapping[str, Any], *, verifier_public_key_hex: str,
                  expected_verifier_id: str, expected_verifier_lock_sha256: str,
                  tokenizer: Any, execution_public_key_hex: str | None = None,
                  expected_execution_attester_id: str | None = None,
                  expected_execution_lock_sha256: str | None = None) -> dict[str, Any]:
    """Return the exact signed canonical-v2 object accepted by ``ce_arm.replay``."""
    if signed_receipt.get("outcome") != "proof":
        raise ActorContractError("CE replay accepts only strictly verified proof receipts")
    _require_signed(signed_receipt, verifier_public_key_hex=verifier_public_key_hex,
                    expected_verifier_id=expected_verifier_id,
                    expected_verifier_lock_sha256=expected_verifier_lock_sha256,
                    tokenizer=tokenizer, execution_public_key_hex=execution_public_key_hex,
                    expected_execution_attester_id=expected_execution_attester_id,
                    expected_execution_lock_sha256=expected_execution_lock_sha256)
    return deepcopy(dict(signed_receipt))


def to_online_v2_receipts(signed_receipt: Mapping[str, Any], *, verifier_public_key_hex: str,
                          expected_verifier_id: str, expected_verifier_lock_sha256: str,
                          tokenizer: Any, execution_public_key_hex: str | None = None,
                          expected_execution_attester_id: str | None = None,
                          expected_execution_lock_sha256: str | None = None):
    """Create Online-v2's existing Search/Verifier/ProofPath dataclasses.

    This imports the reviewed Online-v2 receipt package rather than cloning its
    schema.  The caller must make ``alphaproof_online_v2_arm`` importable.
    """
    _require_signed(signed_receipt, verifier_public_key_hex=verifier_public_key_hex,
                    expected_verifier_id=expected_verifier_id,
                    expected_verifier_lock_sha256=expected_verifier_lock_sha256,
                    tokenizer=tokenizer, execution_public_key_hex=execution_public_key_hex,
                    expected_execution_attester_id=expected_execution_attester_id,
                    expected_execution_lock_sha256=expected_execution_lock_sha256)
    try:
        from alphaproof_online_v2_arm.receipts import (
            CandidateReceipt, ProofPathReceipt, SearchStateReceipt,
            VerifierReceipt, ordered_proof_chain_sha256,
        )
    except ImportError as exc:  # pragma: no cover - deployment wiring failure
        raise RuntimeError("reviewed alphaproof_online_v2_arm package is not importable") from exc

    identity = signed_receipt["behavior_identity"]
    wave_id = f"wave-{int(signed_receipt['request']['wave_index']):04d}"
    # Expand each normalized/executed candidate to one Online row per distinct
    # raw token completion. Byte-identical completions collapse with explicit
    # multiplicity; each row retains a binding to the one real Lean execution.
    searches = []
    verifiers = []
    seen_raw_event_ids: set[str] = set()
    search_by_event: dict[str, Any] = {}
    expanded_by_state: list[tuple[Mapping[str, Any], list[dict[str, Any]]]] = []
    execution_survivor_row: dict[str, str] = {}
    execution_parent: dict[str, str | None] = {}
    execution_depth: dict[str, int] = {}
    terminal_execution_ids: list[str] = []
    raw_owner: dict[tuple[str, tuple[int, ...]], str] = {}
    for state in signed_receipt["search_states"]:
        rows: list[dict[str, Any]] = []
        raw_by_index = {int(raw["candidate_index"]): raw for raw in state["raw_samples"]}
        for executed in state["candidates"]:
            execution_id = executed["event_id"]
            if execution_id in execution_parent:
                raise ActorContractError("duplicate Lean execution event identity")
            execution_parent[execution_id] = executed["parent_event_id"]
            execution_depth[execution_id] = int(executed["depth"])
            if executed["verifier_status"] in {"verified_proof", "verified_disproof"}:
                terminal_execution_ids.append(execution_id)
            grouped: dict[tuple[int, ...], list[tuple[int, tuple[int, int]]]] = {}
            for sample_index, span in zip(executed["sample_indices"], executed["sample_tactic_token_spans"], strict=True):
                raw = raw_by_index[sample_index]
                token_ids = tuple(raw["raw_completion_token_ids"])
                owner_key = (state["generation_request_id"], token_ids)
                previous_owner = raw_owner.setdefault(owner_key, execution_id)
                if previous_owner != execution_id:
                    raise ActorContractError("byte-identical raw completion maps to multiple Lean execution events")
                grouped.setdefault(token_ids, []).append((sample_index, tuple(span)))
            for token_ids, mappings in grouped.items():
                mappings.sort(key=lambda pair: pair[0])
                representative = raw_by_index[mappings[0][0]]
                digest = canonical_sha256({"request_id": state["generation_request_id"], "token_ids": list(token_ids)})
                row_event_id = f"rawrow-{digest}"
                if row_event_id in seen_raw_event_ids:
                    raise ActorContractError("raw row event identity collision")
                seen_raw_event_ids.add(row_event_id)
                prompt_ids = tuple(state["prompt_token_ids"])
                full_ids = prompt_ids + token_ids
                row = {
                    "event_id": row_event_id,
                    "execution_event_id": execution_id,
                    "trajectory_id": executed["trajectory_id"],
                    "action": f"raw:{digest}",
                    "execution_action": executed["action"],
                    "execution_action_token_ids": tuple(executed["action_token_ids"]),
                    "depth": executed["depth"],
                    "parent_execution_event_id": executed["parent_event_id"],
                    "input_ids": full_ids,
                    "attention_mask": (1,) * len(full_ids),
                    "action_mask": (False,) * len(prompt_ids) + (True,) * len(token_ids),
                    "old_logprobs": (0.0,) * len(prompt_ids) + tuple(representative["raw_completion_sampling_logprobs"]),
                    "unwarped_old_logprobs": (0.0,) * len(prompt_ids) + tuple(representative["raw_completion_old_logprobs"]),
                    "action_value": executed["action_value"],
                    "sample_multiplicity": len(mappings),
                    "raw_sample_indices": tuple(index for index, _ in mappings),
                    "tactic_token_spans": tuple(span for _, span in mappings),
                    "finish_reason": representative["finish_reason"],
                    "verifier_status": executed["verifier_status"],
                    "executor_receipt_sha256": executed["executor_receipt_sha256"],
                    "state_after_sha256": executed["state_after_sha256"],
                    "execution_disposition": executed["execution_disposition"],
                    "lean_tactic_executions": executed["lean_tactic_executions"],
                    "is_execution_survivor": executed["survivor_sample_index"] in {index for index, _ in mappings},
                }
                rows.append(row)
                if row["is_execution_survivor"]:
                    if execution_id in execution_survivor_row:
                        raise ActorContractError("execution survivor maps to multiple raw rows")
                    execution_survivor_row[execution_id] = row_event_id
        expanded_by_state.append((state, rows))

    for state, rows in expanded_by_state:
        candidates = []
        for row in rows:
            parent_execution_id = row.pop("parent_execution_event_id")
            if parent_execution_id is None:
                parent_row_id = None
            else:
                try:
                    parent_row_id = execution_survivor_row[parent_execution_id]
                except KeyError as exc:
                    raise ActorContractError("raw row parent lacks a bound execution survivor") from exc
            candidate = CandidateReceipt(
                event_id=row["event_id"], trajectory_id=row["trajectory_id"],
                action=row["action"], depth=row["depth"], parent_event_id=parent_row_id,
                input_ids=row["input_ids"], attention_mask=row["attention_mask"],
                action_mask=row["action_mask"], old_logprobs=row["old_logprobs"],
                action_value=row["action_value"], sample_multiplicity=row["sample_multiplicity"],
                execution_event_id=row["execution_event_id"], execution_action=row["execution_action"],
                execution_action_token_ids=row["execution_action_token_ids"],
                raw_sample_indices=row["raw_sample_indices"], tactic_token_spans=row["tactic_token_spans"],
                finish_reason=row["finish_reason"],
                unwarped_old_logprobs=row["unwarped_old_logprobs"],
            )
            candidates.append(candidate)
        search = SearchStateReceipt.create(
            receipt_id=state["receipt_id"], problem_id=signed_receipt["problem_id"],
            wave_id=wave_id, policy_version=identity["policy_version"],
            behavior_version=identity["behavior_version"],
            behavior_sha256=identity["behavior_sha256"], base_version=identity["base_version"],
            base_sha256=identity["base_sha256"], state_id=state["state_id"],
            tokenizer_sha256=signed_receipt["tokenizer_lock_sha256"],
            eos_token_id=signed_receipt["eos_token_id"], eos_convention="per_candidate",
            prompt_token_ids=tuple(state["prompt_token_ids"]), candidate_set_complete=True,
            expected_candidate_count=len(candidates), candidates=tuple(candidates),
        )
        searches.append(search)
        for candidate, row in zip(candidates, rows, strict=True):
            search_by_event[candidate.event_id] = search
            status = row["verifier_status"]
            # Several distinct raw completions may normalize to one tactic and
            # therefore share one Lean execution.  Only the signed survivor row
            # may carry that execution's terminal result into Online-v2; marking
            # its raw siblings terminal would require duplicate proof paths for
            # the same execution and is rejected by the learner boundary.
            if status in {"verified_proof", "verified_disproof"} and not row["is_execution_survivor"]:
                status = "unresolved"
            verifiers.append(VerifierReceipt.create(
                receipt_id=f"verifier-{candidate.event_id}-{row['executor_receipt_sha256'][:16]}",
                problem_id=signed_receipt["problem_id"], wave_id=wave_id,
                event_id=candidate.event_id, search_receipt_id=search.receipt_id,
                search_receipt_sha256=search.receipt_sha256, status=status,
                terminal_verified=status in {"verified_proof", "verified_disproof"},
                execution_event_id=row["execution_event_id"],
            ))
    verifier_by_event = {item.event_id: item for item in verifiers}
    selected_execution_path = tuple(step["event_id"] for step in signed_receipt["selected_path"])
    paths = []
    used_path_executions: set[str] = set()

    def append_path(execution_path: tuple[str, ...], *, receipt_id: str,
                    expected_outcome: str | None = None) -> None:
        try:
            path_event_ids = tuple(execution_survivor_row[event_id] for event_id in execution_path)
        except KeyError as exc:
            raise ActorContractError(
                "terminal execution path lacks its signed survivor raw row"
            ) from exc
        terminal = verifier_by_event[path_event_ids[-1]]
        outcome_by_status = {
            "verified_proof": "proof",
            "verified_disproof": "disproof",
        }
        outcome = outcome_by_status.get(terminal.status)
        if outcome is None or not terminal.terminal_verified:
            raise ActorContractError("terminal execution path has no verified terminal result")
        if expected_outcome is not None and outcome != expected_outcome:
            raise ActorContractError("signed selected path has the wrong verified status")
        chain = tuple((event_id, search_by_event[event_id].receipt_id,
                       search_by_event[event_id].receipt_sha256,
                       verifier_by_event[event_id].receipt_id,
                       verifier_by_event[event_id].receipt_sha256) for event_id in path_event_ids)
        paths.append(ProofPathReceipt.create(
            receipt_id=receipt_id,
            problem_id=signed_receipt["problem_id"], wave_id=wave_id, outcome=outcome,
            terminal_verifier_receipt_id=terminal.receipt_id,
            terminal_verifier_receipt_sha256=terminal.receipt_sha256,
            ordered_event_chain_sha256=ordered_proof_chain_sha256(chain), event_ids=path_event_ids,
        ))
        used_path_executions.update(execution_path)

    if selected_execution_path:
        append_path(
            selected_execution_path,
            receipt_id=f"path-{signed_receipt['receipt_id']}",
            expected_outcome=signed_receipt["outcome"],
        )

    for terminal_execution_id in terminal_execution_ids:
        if terminal_execution_id in used_path_executions:
            continue
        reversed_execution_path = []
        cursor: str | None = terminal_execution_id
        seen_in_chain: set[str] = set()
        while cursor is not None:
            if cursor in seen_in_chain:
                raise ActorContractError("Lean execution parent links contain a cycle")
            if cursor not in execution_parent:
                raise ActorContractError("terminal Lean execution has an unknown parent")
            seen_in_chain.add(cursor)
            reversed_execution_path.append(cursor)
            cursor = execution_parent[cursor]
        execution_path = tuple(reversed(reversed_execution_path))
        if any(execution_depth[event_id] != index for index, event_id in enumerate(execution_path)):
            raise ActorContractError("terminal Lean execution is not a continuous root-to-terminal chain")
        if used_path_executions.intersection(execution_path):
            raise ActorContractError(
                "terminal Lean execution paths overlap and cannot satisfy the Online-v2 path invariant"
            )
        terminal_digest = canonical_sha256({"terminal_execution_event_id": terminal_execution_id})[:16]
        append_path(
            execution_path,
            receipt_id=f"path-{signed_receipt['receipt_id']}-{terminal_digest}",
        )
    for value in (*searches, *verifiers, *paths):
        value.validate()
    return tuple(searches), tuple(verifiers), tuple(paths)

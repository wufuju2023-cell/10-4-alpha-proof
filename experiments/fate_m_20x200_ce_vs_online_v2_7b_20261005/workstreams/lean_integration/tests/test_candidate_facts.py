from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import threading
import unittest

from fate_reap.candidate_facts import (
    CandidateFactsError,
    SCHEMA,
    _derive_action_span,
    canonical_sha256,
    collect_candidate_facts,
    sha256_file,
)


SERVICE_HASH = "a" * 64


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CandidateFactsTests(unittest.TestCase):
    def test_real_policy_service_v2_receipt_flows_directly_to_collector(self) -> None:
        experiment = Path(__file__).resolve().parents[3]
        for source in (experiment / "workstreams" / "policy_service_bridge" / "src",
                       experiment / "workstreams" / "shared_actor_bridge" / "src"):
            if str(source) not in sys.path:
                sys.path.insert(0, str(source))
        from policy_service_bridge import IdentitySnapshot, InProcessPolicyService
        from policy_service_bridge.receipts import canonical_bytes
        from shared_actor_bridge import GenerationParameters, canonical_sha256
        from shared_actor_bridge.hf_adapter import RawGeneration

        class Tokenizer:
            eos_token_id = 99

            @staticmethod
            def decode(ids, skip_special_tokens=False):
                return "".join("" if token == 99 and skip_special_tokens else
                               "<eos>" if token == 99 else chr(token) for token in ids)

        identity = IdentitySnapshot("p0", "p0", "a" * 64, "base", "b" * 64,
                                    "tok", "d" * 64)

        def generate(**kwargs):
            values = []
            for index in range(2):
                ids = (65 + index, 99)
                evidence = {
                    "request_id": kwargs["request_id"], "candidate_index": index,
                    "request_seed": kwargs["request_seed"], "prompt_token_ids": [1],
                    "raw_completion_token_ids": list(ids),
                    "raw_completion_old_logprobs": [-.1, -.2],
                    "raw_completion_sampling_logprobs": [-.1, -.2], "finish_reason": "stop",
                    "generation_params_sha256": kwargs["generation"].sha256,
                    "behavior_identity_sha256": canonical_sha256({
                        "policy_version": identity.policy_version,
                        "behavior_version": identity.behavior_version,
                        "behavior_sha256": identity.behavior_sha256,
                        "base_version": identity.base_version, "base_sha256": identity.base_sha256,
                        "tokenizer_version": identity.tokenizer_version,
                        "tokenizer_sha256": identity.tokenizer_sha256,
                    }),
                }
                values.append(RawGeneration(
                    kwargs["request_id"], index, kwargs["request_seed"], (1,), ids,
                    (-.1, -.2), (-.1, -.2), "stop", canonical_sha256(evidence), .01, .01,
                ))
            return tuple(values)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = [""]
            service = InProcessPolicyService(
                model=object(), tokenizer=Tokenizer(), receipt_root=root / "receipts",
                identity_provider=lambda _sid: identity,
                activate_session=lambda sid: active.__setitem__(0, sid),
                active_session_provider=lambda: active[0], model_transaction_lock=threading.Lock(),
                generation=GenerationParameters(1.5, .9, 8, 2), seed_namespace="integration",
                served_model_id="base", actor_config_sha256="e" * 64,
                tokenizer_lock_sha256="d" * 64, generator=generate, heartbeat=lambda _event: None,
            )
            body = canonical_bytes({
                "model": "base", "messages": [{"role": "user", "content": "goal"}],
                "n": 2, "temperature": 1.5, "top_p": .9, "max_tokens": 8,
                "logprobs": True,
            })
            _, actor = service.handle(session_id="s1", raw_body=body)
            receipt = json.loads(actor.read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema_version"], "fate.policy_request.v2")
            request_id = receipt["request_id"]
            raw_tree = root / "raw_tree.json"
            raw_tree.write_text("{}\n", encoding="utf-8")
            result_json = root / "result.json"
            result_json.write_text(json.dumps({
                "schema_version": "reap.training.result.v1", "session_id": "s1",
                "solved": True, "status": "solved", "proof_script": "A",
            }) + "\n", encoding="utf-8")

            records = []
            sequence = 0
            for index, action in enumerate(("A", "B")):
                event_id = f"event-{index}"
                start = {
                    "kind": "canonical_candidate", "generation_request_id": request_id,
                    "event_id": event_id, "trajectory_id": "trajectory:s1:t1",
                    "state_id": "state:0", "state_before_payload": "goal",
                    "state_before_sha256": digest("goal"), "depth": 0,
                    "parent_event_id": None, "partial_goal": False, "action": action,
                    "sample_indices": [index], "sample_tactic_token_spans": [[0, 1]],
                    "service_candidate_sha256s": [receipt["candidates"][index]["service_candidate_sha256"]],
                    "survivor_sample_index": index, "action_value": -1.0,
                }
                if index == 0:
                    result = {
                        "kind": "canonical_candidate_result", "generation_request_id": request_id,
                        "event_id": event_id, "trajectory_id": "trajectory:s1:t1",
                        "state_id": "state:0", "state_before_payload": "goal",
                        "state_before_sha256": digest("goal"), "state_after_payload": "",
                        "state_after_sha256": digest(""), "parser_phase": "accepted",
                        "did_execute": True, "lean_tactic_executions": 1,
                        "eval_result": {"ok": None}, "transition_applied": True,
                        "verifier_status": "verified_proof", "execution_disposition": "executed",
                        "search_disposition": "created", "terminal": True, "depth": 0,
                        "parent_event_id": None, "partial_goal": False,
                    }
                else:
                    result = {
                        "kind": "canonical_candidate_result", "generation_request_id": request_id,
                        "event_id": event_id, "trajectory_id": "trajectory:s1:t1",
                        "state_id": "state:0", "state_before_payload": "goal",
                        "state_before_sha256": digest("goal"), "state_after_payload": "goal",
                        "state_after_sha256": digest("goal"), "parser_phase": "rejected",
                        "did_execute": False, "lean_tactic_executions": 0,
                        "eval_result": {"error": {"parseError": "bad"}},
                        "transition_applied": False, "verifier_status": "invalid_tactic",
                        "execution_disposition": "parse_rejected", "search_disposition": "eval_rejected",
                        "terminal": False, "depth": 0, "parent_event_id": None,
                        "partial_goal": False,
                    }
                for record in (start, result):
                    wrapped = {
                        **record, "schema_version": "reap.training.observer.v1",
                        "session_id": "s1", "tree_id": "t1", "policy_version": 0,
                        "sequence": sequence, "monotonic_ns": sequence + 1,
                    }
                    if record["kind"] == "canonical_candidate_result":
                        payload = compact(wrapped)
                        wrapped.update({"executor_receipt_payload": payload,
                                        "executor_receipt_sha256": digest(payload)})
                    records.append(wrapped)
                    sequence += 1
            selected_record = {
                "kind": "canonical_selected_path", "selected_event_ids": ["event-0"],
                "terminal_event_id": "event-0", "proof_script": "A",
                "proof_script_sha256": digest("A"), "outcome": "proof",
                "schema_version": "reap.training.observer.v1", "session_id": "s1",
                "tree_id": "t1", "policy_version": 0, "sequence": sequence,
                "monotonic_ns": sequence + 1,
            }
            selected_payload = compact(selected_record)
            selected_record.update({"selected_path_receipt_payload": selected_payload,
                                    "selected_path_receipt_sha256": digest(selected_payload)})
            records.append(selected_record)
            observer = root / "observer.jsonl"
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            facts = collect_candidate_facts(
                observer=observer, raw_tree=raw_tree, result_json=result_json,
                actor_receipt=actor, output=root / "facts.json", session_id="s1",
                tree_id="t1", expected_raw_count=2,
            )
            self.assertEqual(facts["selected_event_ids"], ["event-0"])

    def test_existing_real_64_choice_receipt_has_exact_raw_spans(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "real_64_choice_action_spans.json"
        fixture = Path("\\\\?\\" + str(fixture)) if os.name == "nt" else fixture
        raw_bytes = fixture.read_bytes()
        self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(),
                         "61751326e8aee28c7bc6b66983b609dddb470f5ff3c27b13a2c66e155c636dff")
        value = json.loads(raw_bytes)
        self.assertEqual(value["source_receipt_sha256"],
                         "bf20bf55fbb11fd102f9800fd55952739f88e7cc0888b59fd340b0fc738bc97e")
        choices = value["openai_response"]["choices"]
        self.assertEqual(len(choices), 64)
        spans = []
        for index, choice in enumerate(choices):
            raw = choice["message"]["content"]
            action, span = _derive_action_span(choice, raw, location=f"real choice {index}")
            self.assertEqual(action, raw)
            spans.append(span)
        self.assertEqual(len(spans), 64)

    def test_think_span_is_end_anchored_not_first_text_match(self) -> None:
        choice = {"logprobs": {"content": [
            {"token": "<think>"}, {"token": "exact h"}, {"token": "</think>"},
            {"token": " exact h"}, {"token": "<eos>"},
        ]}}
        action, span = _derive_action_span(
            choice, "<think>exact h</think> exact h", location="think choice"
        )
        self.assertEqual(action, " exact h")
        self.assertEqual(span, [3, 4])

    def test_think_span_keeps_leading_empty_byte_fragment_token(self) -> None:
        choice = {"logprobs": {"content": [
            {"token": "<think>x</think>"}, {"token": ""},
            {"token": "é"}, {"token": "<eos>"},
        ]}}
        action, span = _derive_action_span(
            choice, "<think>x</think>é", location="fragmented UTF-8 choice"
        )
        self.assertEqual(action, "é")
        self.assertEqual(span, [1, 3])

    def _evidence(self, root: Path) -> tuple[Path, Path, Path, Path]:
        action = "exact h"
        request_id = "request-1"
        choices = []
        candidates = []
        for index in range(2):
            choices.append({
                "index": index,
                "raw_sample_index": index,
                "service_candidate_sha256": SERVICE_HASH,
                "message": {"role": "assistant", "content": action},
                "logprobs": {"content": [
                    {"token": "exact", "logprob": -0.1},
                    {"token": " h", "logprob": -0.2},
                    {"token": "<eos>", "logprob": -0.01},
                ]},
            })
            candidates.append({
                "candidate_index": index, "request_id": request_id,
                "returned_text": action, "service_candidate_sha256": SERVICE_HASH,
            })
        policy = {
            "schema_version": "fate.policy_request.v2", "status": "committed",
            "request_id": request_id, "candidates": candidates,
            "openai_response": {"id": request_id, "choices": choices},
        }
        policy["receipt_sha256"] = canonical_sha256(policy)
        actor = root / "policy.json"
        actor.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
        raw_tree = root / "raw_tree.json"
        raw_tree.write_text("{}\n", encoding="utf-8")
        terminal_result = root / "result.json"
        terminal_result.write_text(json.dumps({
            "schema_version": "reap.training.result.v1", "session_id": "s1",
            "solved": True, "status": "solved", "proof_script": action,
        }) + "\n", encoding="utf-8")

        start = {
            "kind": "canonical_candidate", "generation_request_id": request_id,
            "event_id": "event-0", "trajectory_id": "trajectory:s1:t1", "state_id": "state:0",
            "state_before_payload": "goal", "state_before_sha256": digest("goal"),
            "depth": 0, "parent_event_id": None, "partial_goal": False, "action": action,
            "sample_indices": [0, 1], "sample_tactic_token_spans": [[0, 2], [0, 2]],
            "service_candidate_sha256s": [SERVICE_HASH, SERVICE_HASH],
            "survivor_sample_index": 1, "action_value": -1.0,
        }
        result_core = {
            "kind": "canonical_candidate_result", "generation_request_id": request_id,
            "event_id": "event-0", "trajectory_id": "trajectory:s1:t1", "state_id": "state:0",
            "state_before_payload": "goal", "state_before_sha256": digest("goal"),
            "state_after_payload": "", "state_after_sha256": digest(""),
            "parser_phase": "accepted", "did_execute": True,
            "lean_tactic_executions": 1, "eval_result": {"ok": None},
            "transition_applied": True, "verifier_status": "verified_proof",
            "execution_disposition": "executed", "search_disposition": "created",
            "terminal": True, "depth": 0, "parent_event_id": None,
            "partial_goal": False,
        }
        selected = {
            "kind": "canonical_selected_path", "selected_event_ids": ["event-0"],
            "terminal_event_id": "event-0", "proof_script": action,
            "proof_script_sha256": digest(action), "outcome": "proof",
        }
        observer = root / "observer.jsonl"
        def wrap(sequence: int, record: dict) -> dict:
            return {
                **record, "schema_version": "reap.training.observer.v1",
                "session_id": "s1", "tree_id": "t1", "policy_version": 0,
                "sequence": sequence, "monotonic_ns": sequence + 1,
            }
        wrapped_start = wrap(0, start)
        wrapped_result_core = wrap(1, result_core)
        receipt_payload = compact(wrapped_result_core)
        wrapped_result = {
            **wrapped_result_core, "executor_receipt_payload": receipt_payload,
            "executor_receipt_sha256": digest(receipt_payload),
        }
        wrapped_selected = wrap(2, selected)
        selected_payload = compact(wrapped_selected)
        wrapped_selected.update({
            "selected_path_receipt_payload": selected_payload,
            "selected_path_receipt_sha256": digest(selected_payload),
        })
        wrapped = [wrapped_start, wrapped_result, wrapped_selected]
        observer.write_text("".join(json.dumps(item) + "\n" for item in wrapped), encoding="utf-8")
        return observer, raw_tree, terminal_result, actor

    def test_collects_new_events_and_exclusively_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            output = root / "candidate-facts.json"
            facts = collect_candidate_facts(
                observer=observer, raw_tree=raw_tree, result_json=result_json,
                actor_receipt=actor, output=output,
                session_id="s1", tree_id="t1", expected_raw_count=2,
            )
            self.assertEqual(facts["schema_version"], SCHEMA)
            self.assertEqual(facts["candidates"][0]["sample_indices"], [0, 1])
            self.assertEqual(facts["policy_receipt_sha256"], sha256_file(actor))
            self.assertTrue(output.is_file())
            with self.assertRaisesRegex(CandidateFactsError, "overwrite"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=output,
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )

    def test_refuses_legacy_observer_without_canonical_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            observer.write_text(json.dumps({
                "schema_version": "reap.training.observer.v1", "session_id": "s1",
                "tree_id": "t1", "sequence": 0, "kind": "generation",
            }) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "starts/results"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json, actor_receipt=actor,
                    output=root / "facts.json", session_id="s1", tree_id="t1",
                    expected_raw_count=2,
                )

    def test_pairing_failure_reports_bounded_missing_event_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            records = [json.loads(line) for line in observer.read_text(encoding="utf-8").splitlines()]
            event_id = records[0]["event_id"]
            records = [record for record in records
                       if record.get("kind") != "canonical_candidate_result"]
            for sequence, record in enumerate(records):
                record["sequence"] = sequence
            observer.write_text(
                "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
            )
            with self.assertRaises(CandidateFactsError) as raised:
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=root / "facts.json",
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )
            message = str(raised.exception)
            self.assertIn("starts=1, results=0", message)
            self.assertIn(f"missing_result_ids=['{event_id}']", message)
            self.assertIn("orphan_result_count=0", message)

    def test_refuses_unprovable_token_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            value = json.loads(actor.read_text(encoding="utf-8"))
            value["openai_response"]["choices"][0]["logprobs"]["content"][0]["token"] = "trimmed"
            value["receipt_sha256"] = canonical_sha256(
                {key: item for key, item in value.items() if key != "receipt_sha256"}
            )
            actor.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "response-content token boundary"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json, actor_receipt=actor,
                    output=root / "facts.json", session_id="s1", tree_id="t1",
                    expected_raw_count=2,
                )

    def test_refuses_contradictory_verified_proof_even_with_rehashed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            records = [json.loads(line) for line in observer.read_text(encoding="utf-8").splitlines()]
            result = records[1]
            result.update({
                "terminal": False, "transition_applied": False,
                "eval_result": {"error": {"parseError": "bad"}},
                "parser_phase": "rejected", "did_execute": False,
                "execution_disposition": "parse_rejected", "lean_tactic_executions": 0,
                "state_after_payload": "goal", "state_after_sha256": digest("goal"),
            })
            payload_record = {key: value for key, value in result.items()
                              if key not in {"executor_receipt_payload", "executor_receipt_sha256"}}
            payload = compact(payload_record)
            result["executor_receipt_payload"] = payload
            result["executor_receipt_sha256"] = digest(payload)
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "contradictory parse-rejected"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=root / "facts.json",
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )

    def test_executor_hash_payload_must_bind_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            records = [json.loads(line) for line in observer.read_text(encoding="utf-8").splitlines()]
            payload = json.loads(records[1]["executor_receipt_payload"])
            payload["tree_id"] = "substituted-tree"
            encoded = compact(payload)
            records[1]["executor_receipt_payload"] = encoded
            records[1]["executor_receipt_sha256"] = digest(encoded)
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "payload/event mismatch"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=root / "facts.json",
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )

    def test_selected_proof_must_equal_terminal_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            result = json.loads(result_json.read_text(encoding="utf-8"))
            result["proof_script"] = "exact other"
            result_json.write_text(json.dumps(result) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "does not match terminal"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=root / "facts.json",
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )

    def test_focus_local_closure_cannot_claim_verified_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer, raw_tree, result_json, actor = self._evidence(root)
            records = [json.loads(line) for line in observer.read_text(encoding="utf-8").splitlines()]
            records[0]["partial_goal"] = True
            records[1]["partial_goal"] = True
            payload_record = {key: value for key, value in records[1].items()
                              if key not in {"executor_receipt_payload", "executor_receipt_sha256"}}
            payload = compact(payload_record)
            records[1]["executor_receipt_payload"] = payload
            records[1]["executor_receipt_sha256"] = digest(payload)
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            with self.assertRaisesRegex(CandidateFactsError, "contradictory successful"):
                collect_candidate_facts(
                    observer=observer, raw_tree=raw_tree, result_json=result_json,
                    actor_receipt=actor, output=root / "facts.json",
                    session_id="s1", tree_id="t1", expected_raw_count=2,
                )

    def test_two_step_selected_path_has_stable_trajectory_and_state_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _observer, raw_tree, result_json, actor = self._evidence(root)
            policy = json.loads(actor.read_text(encoding="utf-8"))
            for index, action in enumerate(("first", "second")):
                policy["candidates"][index]["returned_text"] = action
                policy["openai_response"]["choices"][index]["message"]["content"] = action
                policy["openai_response"]["choices"][index]["logprobs"]["content"] = [
                    {"token": action, "logprob": -.1}, {"token": "<eos>", "logprob": -.2},
                ]
            policy["receipt_sha256"] = canonical_sha256(
                {key: value for key, value in policy.items() if key != "receipt_sha256"}
            )
            actor.write_text(json.dumps(policy) + "\n", encoding="utf-8")
            request_id = policy["request_id"]
            records = []
            states = (("goal", "mid"), ("mid", ""))
            for index, (action, (before, after)) in enumerate(zip(("first", "second"), states)):
                event_id = f"event-{index}"
                common = {
                    "generation_request_id": request_id, "event_id": event_id,
                    "trajectory_id": "trajectory:s1:t1", "state_id": f"state:{index}",
                    "depth": index, "parent_event_id": None if index == 0 else "event-0",
                    "partial_goal": False,
                }
                start = {
                    **common, "kind": "canonical_candidate", "state_before_payload": before,
                    "state_before_sha256": digest(before), "action": action,
                    "sample_indices": [index], "sample_tactic_token_spans": [[0, 1]],
                    "service_candidate_sha256s": [SERVICE_HASH],
                    "survivor_sample_index": index, "action_value": -1.0,
                }
                result = {
                    **common, "kind": "canonical_candidate_result",
                    "state_before_payload": before, "state_before_sha256": digest(before),
                    "state_after_payload": after, "state_after_sha256": digest(after),
                    "parser_phase": "accepted", "did_execute": True,
                    "lean_tactic_executions": 1, "eval_result": {"ok": None},
                    "transition_applied": True,
                    "verifier_status": "unresolved" if index == 0 else "verified_proof",
                    "execution_disposition": "executed", "search_disposition": "created",
                    "terminal": index == 1,
                }
                for record in (start, result):
                    sequence = len(records)
                    wrapped = {
                        **record, "schema_version": "reap.training.observer.v1",
                        "session_id": "s1", "tree_id": "t1", "policy_version": 0,
                        "sequence": sequence, "monotonic_ns": sequence + 1,
                    }
                    if record["kind"] == "canonical_candidate_result":
                        payload = compact(wrapped)
                        wrapped.update({"executor_receipt_payload": payload,
                                        "executor_receipt_sha256": digest(payload)})
                    records.append(wrapped)
            script = "first\nsecond"
            selected_record = {
                "kind": "canonical_selected_path", "selected_event_ids": ["event-0", "event-1"],
                "terminal_event_id": "event-1", "proof_script": script,
                "proof_script_sha256": digest(script), "outcome": "proof",
                "schema_version": "reap.training.observer.v1", "session_id": "s1",
                "tree_id": "t1", "policy_version": 0, "sequence": len(records),
                "monotonic_ns": len(records) + 1,
            }
            selected_payload = compact(selected_record)
            selected_record.update({"selected_path_receipt_payload": selected_payload,
                                    "selected_path_receipt_sha256": digest(selected_payload)})
            records.append(selected_record)
            observer = root / "observer-two-step.jsonl"
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            result_json.write_text(json.dumps({
                "schema_version": "reap.training.result.v1", "session_id": "s1",
                "solved": True, "status": "solved", "proof_script": script,
            }) + "\n", encoding="utf-8")
            facts = collect_candidate_facts(
                observer=observer, raw_tree=raw_tree, result_json=result_json,
                actor_receipt=actor, output=root / "facts.json", session_id="s1",
                tree_id="t1", expected_raw_count=2,
            )
            self.assertEqual(facts["selected_event_ids"], ["event-0", "event-1"])

    def test_branching_and_path_is_explicitly_fail_closed_in_runtime_patch(self) -> None:
        patch = (Path(__file__).resolve().parents[1] / "runtime" / "patches" /
                 "0004-canonical-candidate-provenance.patch").read_text(encoding="utf-8")
        self.assertIn("cannot represent branching AND node", patch)


if __name__ == "__main__":
    unittest.main()

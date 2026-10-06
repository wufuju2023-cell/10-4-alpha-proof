from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fate_reap.candidate_facts import CandidateFactsError, rebuild_candidate_facts_envelope
from fate_reap.e2e_receipt_join import (
    CANDIDATE_FACTS_SCHEMA,
    JoinError,
    _assert_producer_evidence_unchanged,
    _canonical_sha256,
    _run_paths,
    audit_run,
    run_join,
    sha256_file,
)


H = "a" * 64


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class E2EReceiptJoinTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        run = root / "run"
        raw_tree = run / "raw_tree.json"
        dump(raw_tree, {"root_index": 0, "nodes": []})
        result = run / "session" / "result.json"
        dump(result, {
            "schema_version": "reap.training.result.v1", "session_id": "s1",
            "solved": True, "status": "solved", "proof_script": "exact h",
        })
        sessions = run / "sessions.jsonl"
        dump(sessions, {"session_id": "s1", "source_id": "s1", "source_sha256": H,
                        "generated_sha256": H, "model_sha256": H})
        policy = run / "actor_receipts" / "s1" / "policy_requests" / "00000001-r.json"
        generation_receipt = {"request_id": "r1", "outputs": [{"candidate_index": i} for i in range(64)]}
        choices = [{
            "index": i, "raw_sample_index": i, "service_candidate_sha256": H,
            "message": {"role": "assistant", "content": "exact h"},
            "logprobs": {"content": [
                {"token": "exact", "logprob": -0.1},
                {"token": " h", "logprob": -0.2},
                {"token": "<eos>", "logprob": -0.01},
            ]},
        } for i in range(64)]
        policy_value = {
            "schema_version": "fate.policy_request.v2", "status": "committed",
            "session_id": "s1", "request_id": "r1",
            "candidates": [{
                "candidate_index": i, "request_id": "r1", "returned_text": "exact h",
                "service_candidate_sha256": H,
            } for i in range(64)],
            "openai_response": {"id": "r1", "choices": choices},
            "generation_receipt": generation_receipt,
            "generation_receipt_sha256": _canonical_sha256(generation_receipt),
        }
        policy_value["receipt_sha256"] = _canonical_sha256(policy_value)
        dump(policy, policy_value)
        state_before = hashlib.sha256(b"goal").hexdigest()
        state_after = hashlib.sha256(b"").hexdigest()
        common = {
            "schema_version": "reap.training.observer.v1", "session_id": "s1",
            "tree_id": "t1", "policy_version": 0,
        }
        start = {
            **common, "sequence": 0, "monotonic_ns": 1, "kind": "canonical_candidate",
            "generation_request_id": "r1", "event_id": "event-0",
            "trajectory_id": "trajectory:s1:t1", "state_id": "state-0",
            "state_before_payload": "goal", "state_before_sha256": state_before,
            "depth": 0, "parent_event_id": None, "partial_goal": False,
            "action": "exact h", "sample_indices": list(range(64)),
            "sample_tactic_token_spans": [[0, 2] for _ in range(64)],
            "service_candidate_sha256s": [H for _ in range(64)],
            "survivor_sample_index": 0, "action_value": 0.0,
        }
        result_core = {
            **common, "sequence": 1, "monotonic_ns": 2,
            "kind": "canonical_candidate_result", "generation_request_id": "r1",
            "event_id": "event-0", "trajectory_id": "trajectory:s1:t1",
            "state_id": "state-0", "state_before_payload": "goal",
            "state_before_sha256": state_before, "state_after_payload": "",
            "state_after_sha256": state_after, "parser_phase": "accepted",
            "did_execute": True, "lean_tactic_executions": 1,
            "eval_result": {"ok": None}, "transition_applied": True,
            "verifier_status": "verified_proof", "execution_disposition": "executed",
            "search_disposition": "created", "terminal": True, "depth": 0,
            "parent_event_id": None, "partial_goal": False,
        }
        result_payload = json.dumps(
            result_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        result_event = {
            **result_core, "executor_receipt_payload": result_payload,
            "executor_receipt_sha256": hashlib.sha256(result_payload.encode()).hexdigest(),
        }
        selected_core = {
            **common, "sequence": 2, "monotonic_ns": 3,
            "kind": "canonical_selected_path", "selected_event_ids": ["event-0"],
            "terminal_event_id": "event-0", "proof_script": "exact h",
            "proof_script_sha256": hashlib.sha256(b"exact h").hexdigest(),
            "outcome": "proof",
        }
        selected_payload = json.dumps(
            selected_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        selected_event = {
            **selected_core, "selected_path_receipt_payload": selected_payload,
            "selected_path_receipt_sha256": hashlib.sha256(selected_payload.encode()).hexdigest(),
        }
        checkpoint = {
            **common, "sequence": 3, "monotonic_ns": 4, "kind": "checkpoint",
            "root_is_solved": True,
        }
        observer_records = [start, result_event, selected_event, checkpoint]
        observer = run / "observer.jsonl"
        observer.parent.mkdir(parents=True, exist_ok=True)
        observer.write_text("".join(json.dumps(x) + "\n" for x in observer_records), encoding="utf-8")
        report = run / "report.json"
        dump(report, {
            "schema_version": "fate.policy_service.real_reap_e2e_smoke.v1",
            "result": "PASS", "session_id": "s1",
            "artifacts": {
                "manifest": {"sha256": sha256_file(sessions)},
                "observer": {"sha256": sha256_file(observer)},
                "raw_tree": {"sha256": sha256_file(raw_tree)},
                "policy_receipts": [{"sha256": sha256_file(policy)}],
                "session": {"result.json": {"sha256": sha256_file(result)}},
            },
        })
        dump(run / "DONE.json", {"state": "DONE", "report_sha256": sha256_file(report)})
        return run

    def _multi_run(self, root: Path) -> Path:
        """Upgrade the bounded fixture to a real two-request/two-state path."""
        run = self._run(root)
        first_path = next(run.glob("actor_receipts/*/policy_requests/*.json"))
        template = json.loads(first_path.read_text(encoding="utf-8"))
        policy_paths = []
        for request_index, action in enumerate(("first", "second"), 1):
            request_id = f"r{request_index}"
            policy = json.loads(json.dumps(template))
            policy["request_id"] = request_id
            policy["generation_receipt"]["request_id"] = request_id
            policy["generation_receipt_sha256"] = _canonical_sha256(policy["generation_receipt"])
            for index, candidate in enumerate(policy["candidates"]):
                candidate.update({"request_id": request_id, "returned_text": action})
                choice = policy["openai_response"]["choices"][index]
                choice["message"]["content"] = action
                choice["logprobs"]["content"] = [
                    {"token": action, "logprob": -0.1},
                    {"token": "<eos>", "logprob": -0.01},
                ]
            policy["receipt_sha256"] = _canonical_sha256({
                key: value for key, value in policy.items() if key != "receipt_sha256"
            })
            path = first_path.with_name(f"0000000{request_index}-{request_id}.json")
            dump(path, policy)
            policy_paths.append(path)
        first_path.unlink()

        records = []
        state_specs = (("goal", "mid", "unresolved"), ("mid", "", "verified_proof"))
        for index, (action, (before, after, status)) in enumerate(
            zip(("first", "second"), state_specs, strict=True)
        ):
            common = {
                "schema_version": "reap.training.observer.v1", "session_id": "s1",
                "tree_id": "t1", "policy_version": 0,
                "generation_request_id": f"r{index + 1}", "event_id": f"event-{index}",
                "trajectory_id": "trajectory:s1:t1", "state_id": f"state-{index}",
                "depth": index, "parent_event_id": None if index == 0 else "event-0",
                "partial_goal": False,
            }
            start = {
                **common, "sequence": len(records), "monotonic_ns": len(records) + 1,
                "kind": "canonical_candidate", "state_before_payload": before,
                "state_before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                "action": action, "sample_indices": list(range(64)),
                "sample_tactic_token_spans": [[0, 1] for _ in range(64)],
                "service_candidate_sha256s": [H for _ in range(64)],
                "survivor_sample_index": 0, "action_value": -1.0,
            }
            records.append(start)
            result_core = {
                **common, "sequence": len(records), "monotonic_ns": len(records) + 1,
                "kind": "canonical_candidate_result", "state_before_payload": before,
                "state_before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                "state_after_payload": after,
                "state_after_sha256": hashlib.sha256(after.encode()).hexdigest(),
                "parser_phase": "accepted", "did_execute": True,
                "lean_tactic_executions": 1, "eval_result": {"ok": None},
                "transition_applied": True, "verifier_status": status,
                "execution_disposition": "executed", "search_disposition": "created",
                "terminal": index == 1,
            }
            payload = json.dumps(result_core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            records.append({
                **result_core, "executor_receipt_payload": payload,
                "executor_receipt_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            })
        script = "first\nsecond"
        selected_core = {
            "schema_version": "reap.training.observer.v1", "session_id": "s1",
            "tree_id": "t1", "policy_version": 0, "sequence": len(records),
            "monotonic_ns": len(records) + 1, "kind": "canonical_selected_path",
            "selected_event_ids": ["event-0", "event-1"],
            "terminal_event_id": "event-1", "proof_script": script,
            "proof_script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "outcome": "proof",
        }
        selected_payload = json.dumps(
            selected_core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        records.append({
            **selected_core, "selected_path_receipt_payload": selected_payload,
            "selected_path_receipt_sha256": hashlib.sha256(selected_payload.encode()).hexdigest(),
        })
        records.append({
            "schema_version": "reap.training.observer.v1", "session_id": "s1",
            "tree_id": "t1", "policy_version": 0, "sequence": len(records),
            "monotonic_ns": len(records) + 1, "kind": "checkpoint", "root_is_solved": True,
        })
        observer = run / "observer.jsonl"
        observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
        result = run / "session" / "result.json"
        dump(result, {
            "schema_version": "reap.training.result.v1", "session_id": "s1",
            "solved": True, "status": "solved", "proof_script": script,
        })
        report_path = run / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["artifacts"]["observer"]["sha256"] = sha256_file(observer)
        report["artifacts"]["session"]["result.json"]["sha256"] = sha256_file(result)
        report["artifacts"]["policy_receipts"] = [
            {"sha256": sha256_file(path)} for path in policy_paths
        ]
        dump(report_path, report)
        dump(run / "DONE.json", {"state": "DONE", "report_sha256": sha256_file(report_path)})
        return run

    def _facts(self, run: Path, output: Path) -> dict:
        policy = next(run.glob("actor_receipts/*/policy_requests/*.json"))
        facts = rebuild_candidate_facts_envelope(
            observer=run / "observer.jsonl", raw_tree=run / "raw_tree.json",
            result_json=run / "session" / "result.json", actor_receipts=(policy,),
            session_id="s1", tree_id="t1", expected_raw_count=64,
        )
        dump(output, facts)
        return facts

    def test_missing_executor_facts_is_reported_before_any_signing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = audit_run(self._run(Path(tmp)))
        self.assertEqual(report["status"], "blocked_missing_executor_evidence")
        self.assertEqual(report["observed_counts"]["raw_policy_samples"], 64)
        self.assertEqual(report["gaps"][0]["code"], "missing_candidate_facts_receipt")
        self.assertFalse(report["private_key_read"])
        self.assertFalse(report["lean_invoked"])
        self.assertFalse(report["signed"])
        self.assertFalse(report["converted"])

    def test_complete_bound_candidate_facts_pass_structural_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root)
            facts_path = root / "candidate-facts.json"
            self._facts(run, facts_path)
            report = audit_run(run, candidate_facts=facts_path)
            sessions_sha256 = sha256_file(run / "sessions.jsonl")
            result_sha256 = sha256_file(run / "session" / "result.json")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["gaps"], [])
        self.assertFalse(report["private_key_read"])
        self.assertEqual(report["immutable_inputs"]["sessions_sha256"], sessions_sha256)
        self.assertEqual(report["immutable_inputs"]["result_sha256"], result_sha256)

    def test_tampered_session_manifest_fails_report_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            with (run / "sessions.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(JoinError, "immutable session manifest hash mismatch"):
                audit_run(run)

    def test_tampered_result_fails_report_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            result_path = run / "session" / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["proof_script"] = "exact stale"
            dump(result_path, result)
            with self.assertRaisesRegex(JoinError, "immutable session result hash mismatch"):
                audit_run(run)

    def test_sessions_or_result_change_after_audit_fails_presign_recheck(self) -> None:
        for relative_path in ("sessions.jsonl", "session/result.json"):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run = self._run(root)
                facts_path = root / "facts.json"
                self._facts(run, facts_path)
                audit = audit_run(run, candidate_facts=facts_path)
                with (run / relative_path).open("a", encoding="utf-8") as handle:
                    handle.write("\n")
                with self.assertRaisesRegex(
                    JoinError, "producer evidence changed before private-key access"
                ):
                    _assert_producer_evidence_unchanged(
                        _run_paths(run), audit, facts_path
                    )

    def test_execute_with_incomplete_facts_never_reads_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root)
            facts_path = root / "incomplete.json"
            dump(facts_path, {"schema_version": CANDIDATE_FACTS_SCHEMA})
            with patch("fate_reap.e2e_receipt_join._load_private_key") as load_key:
                with self.assertRaisesRegex(JoinError, "candidate facts are incomplete"):
                    run_join(SimpleNamespace(run_root=run, candidate_facts=facts_path))
                load_key.assert_not_called()

    def test_tampered_executor_hash_with_valid_sidecar_self_hash_never_reads_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root)
            facts_path = root / "facts.json"
            facts = self._facts(run, facts_path)
            facts["states"][0]["candidates"][0]["executor_receipt_sha256"] = "f" * 64
            facts["receipt_sha256"] = _canonical_sha256({
                key: value for key, value in facts.items() if key != "receipt_sha256"
            })
            dump(facts_path, facts)
            with patch("fate_reap.e2e_receipt_join._load_private_key") as load_key:
                with self.assertRaisesRegex(JoinError, "candidate facts are incomplete"):
                    run_join(SimpleNamespace(run_root=run, candidate_facts=facts_path))
                load_key.assert_not_called()

    def test_tampered_state_hash_with_valid_sidecar_self_hash_never_reads_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root)
            facts_path = root / "facts.json"
            facts = self._facts(run, facts_path)
            facts["states"][0]["candidates"][0]["state_after_sha256"] = "f" * 64
            facts["receipt_sha256"] = _canonical_sha256({
                key: value for key, value in facts.items() if key != "receipt_sha256"
            })
            dump(facts_path, facts)
            with patch("fate_reap.e2e_receipt_join._load_private_key") as load_key:
                with self.assertRaisesRegex(JoinError, "candidate facts are incomplete"):
                    run_join(SimpleNamespace(run_root=run, candidate_facts=facts_path))
                load_key.assert_not_called()

    def test_two_policy_requests_reconstruct_two_canonical_search_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._multi_run(root)
            policies = tuple(sorted(run.glob("actor_receipts/*/policy_requests/*.json")))
            facts = rebuild_candidate_facts_envelope(
                observer=run / "observer.jsonl", raw_tree=run / "raw_tree.json",
                result_json=run / "session" / "result.json", actor_receipts=policies,
                session_id="s1", tree_id="t1", expected_raw_count=64,
            )
            facts_path = root / "multi-facts.json"
            dump(facts_path, facts)
            report = audit_run(run, candidate_facts=facts_path)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["observed_counts"]["policy_requests"], 2)
        self.assertEqual(report["observed_counts"]["raw_policy_samples"], 128)
        self.assertEqual(
            [state["generation_request_id"] for state in facts["states"]], ["r1", "r2"]
        )
        self.assertEqual(facts["selected_event_ids"], ["event-0", "event-1"])
        self.assertEqual(facts["states"][1]["state_sha256"],
                         facts["states"][0]["candidates"][0]["state_after_sha256"])

    def test_policy_request_cannot_be_reused_for_a_distinct_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._multi_run(Path(tmp))
            observer = run / "observer.jsonl"
            records = [json.loads(line) for line in observer.read_text(encoding="utf-8").splitlines()]
            for record in records:
                if record.get("event_id") == "event-1":
                    record["generation_request_id"] = "r1"
                    if record.get("kind") == "canonical_candidate":
                        record["action"] = "first"
                    if record.get("kind") == "canonical_candidate_result":
                        core = {key: value for key, value in record.items()
                                if key not in {"executor_receipt_payload", "executor_receipt_sha256"}}
                        payload = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        record["executor_receipt_payload"] = payload
                        record["executor_receipt_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
            observer.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            policies = tuple(sorted(run.glob("actor_receipts/*/policy_requests/*.json")))
            with self.assertRaisesRegex(CandidateFactsError, "reused across distinct search states"):
                rebuild_candidate_facts_envelope(
                    observer=observer, raw_tree=run / "raw_tree.json",
                    result_json=run / "session" / "result.json", actor_receipts=policies,
                    session_id="s1", tree_id="t1", expected_raw_count=64,
                )


if __name__ == "__main__":
    unittest.main()

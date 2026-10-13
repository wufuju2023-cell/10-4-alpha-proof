from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fate_reap.canonical_receipt import (object_hash, sign_actor_receipt,
                                         validate_unsigned_actor_receipt)
from fate_reap.checkpoint import audit_checkpoints, write_checkpoint_ack
from fate_reap.evidence import audit_wall_clock, classify_evidence, sha256_file
from fate_reap.runner import Session, build_session_env
from fate_reap.session_builder import SearchOptions, compile_source, load_records, write_sessions
from fate_reap.strict_replay import ReplayInputs, materialize, run_strict_replay, scan_forbidden_tokens


SOURCE = """import Mathlib

namespace FateCurriculum

theorem fate_m_999_v001 (h : True) : True := by
  sorry

end FateCurriculum
"""
HASH = "a" * 64
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY_HEX = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
).hex()
VERIFIER_LOCK_HASH = "9" * 64
VERIFIER_ID = "lean428-kernel"


def actor_receipt(statement_sha256: str = HASH) -> dict:
    request = {
        "problem_id": "s1", "statement_sha256": statement_sha256, "wave_index": 1,
        "actor_config_sha256": "1" * 64, "budget_config_sha256": "2" * 64,
        "initial_state_sha256": "3" * 64,
    }
    path = [{
        "step_index": 0, "state_before_sha256": "3" * 64,
        "state_after_sha256": "4" * 64, "prompt": "prove it",
        "prompt_token_ids": [1, 2], "raw_completion_token_ids": [3, 4],
        "tactic_token_span": [0, 2], "action": "exact h", "value_target": -1.0,
    }]
    return {
        "schema_version": 2, "receipt_id": "receipt-s1", "attempt_id": "attempt-s1",
        "problem_id": "s1", "statement_sha256": statement_sha256, "outcome": "proof",
        "actor_config_sha256": "1" * 64, "budget_config_sha256": "2" * 64,
        "tokenizer_lock_sha256": "5" * 64, "request": request,
        "request_sha256": object_hash(request), "selected_path": path,
        "verification": None,
        "cost": {"generated_tokens": 2, "lean_tactic_executions": 1},
    }


def set_actor_linear_path(receipt: dict, length: int) -> dict:
    initial = receipt["request"]["initial_state_sha256"]
    states = [initial] + [
        hashlib.sha256(f"actor-state-{index}".encode()).hexdigest()
        for index in range(1, length + 1)
    ]
    receipt["selected_path"] = [
        {
            "step_index": index,
            "state_before_sha256": states[index],
            "state_after_sha256": states[index + 1],
            "prompt": f"state {index}",
            "prompt_token_ids": [1, 2],
            "raw_completion_token_ids": [3, 4],
            "tactic_token_span": [0, 2],
            "action": f"tactic_{index}",
            "value_target": -(length - index),
        }
        for index in range(length)
    ]
    receipt["cost"] = {
        "generated_tokens": 2 * length,
        "lean_tactic_executions": length,
    }
    return receipt


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def result_record(*, solved: object = False, status: object = "exhausted", proof: object = None) -> dict:
    return {
        "schema_version": "reap.training.result.v1", "session_id": "s1",
        "solved": solved, "status": status, "proof_script": proof,
        "error": None, "elapsed_ns": 1,
    }


def wall_event(content: str = "rfl") -> dict:
    raw = json.dumps({"choices": [{"message": {"content": content}}]})
    return {"name": "tactic_gen", "extra": {"result": raw}}


def service_record(
    request_id: str = "r1", *, status: str = "ok", service: str = "policy",
    session_id: str = "s1", tree_id: str = "tree1",
) -> dict:
    return {
        "schema_version": "fate.service.request.v1", "request_id": request_id,
        "session_id": session_id, "tree_id": tree_id, "step": 0,
        "policy_version": 0, "service": service, "terminal": True,
        "status": status, "http_status": 200 if status == "ok" else 500,
        "parse_status": "ok" if status == "ok" else "error", "choice_count": 1 if status == "ok" else 0,
        "model_sha256": HASH, "request_sha256": HASH, "response_sha256": HASH,
    }


def make_session_dir(root: Path, result: dict, services: list[dict] | None = None) -> None:
    dump(root / "result.json", result)
    dump(root / "raw_tree.json", {"nodes": []})
    (root / "wall_clock.jsonl").write_text(json.dumps(wall_event()) + "\n", encoding="utf-8")
    records = services if services is not None else [service_record()]
    (root / "service_requests.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )


class CanonicalValueTargetTests(unittest.TestCase):
    def _validate(self, receipt: dict) -> tuple[str, str, str]:
        return validate_unsigned_actor_receipt(
            receipt, problem_id="s1", statement_sha256=receipt["statement_sha256"]
        )

    def test_correct_multistep_targets_are_accepted(self) -> None:
        receipt = set_actor_linear_path(actor_receipt(), 3)
        self._validate(receipt)
        self.assertEqual(
            [step["value_target"] for step in receipt["selected_path"]],
            [-3, -2, -1],
        )

    def test_one_step_minus_64_is_rejected_and_signer_cannot_bypass(self) -> None:
        receipt = actor_receipt()
        receipt["selected_path"][0]["value_target"] = -64
        with self.assertRaisesRegex(ValueError, "value_target mismatch"):
            self._validate(receipt)
        with self.assertRaisesRegex(ValueError, "value_target mismatch"):
            sign_actor_receipt(
                receipt,
                verifier_lock={"verifier_id": VERIFIER_ID,
                               "ed25519_public_key_hex": PUBLIC_KEY_HEX},
                verifier_lock_sha256=VERIFIER_LOCK_HASH,
                request_sha256=receipt["request_sha256"],
                statement_sha256=receipt["statement_sha256"],
                selected_path_sha256=object_hash(receipt["selected_path"]),
                initial_state_sha256="3" * 64,
                final_state_sha256="4" * 64,
                kernel_receipt_sha256=HASH,
                private_key_path=Path("must-not-be-read.key"),
            )

    def test_reversed_missing_and_duplicate_indices_are_rejected(self) -> None:
        for indices in ([1, 0], [0, 2], [0, 0]):
            with self.subTest(indices=indices):
                receipt = set_actor_linear_path(actor_receipt(), 2)
                for step, index in zip(receipt["selected_path"], indices):
                    step["step_index"] = index
                with self.assertRaisesRegex(ValueError, "malformed step"):
                    self._validate(receipt)

    def test_65_step_path_is_rejected_not_clamped(self) -> None:
        with self.assertRaisesRegex(ValueError, "65 tactics"):
            self._validate(set_actor_linear_path(actor_receipt(), 65))


class BuilderTests(unittest.TestCase):
    def test_compile_is_narrow_and_real(self) -> None:
        compiled = compile_source(SOURCE, SearchOptions(num_samples=2, max_steps=3))
        self.assertTrue(compiled.startswith("import ReapRuntime"))
        self.assertIn("set_option reap.num_samples 2", compiled)
        self.assertIn("reapTrainingMCTS", compiled)
        self.assertNotIn("  sorry", compiled)

    def test_ambiguous_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compile_source(SOURCE + "\nexample : True := by\n  sorry\n", SearchOptions())

    def test_manifest_binds_top_level_dataset_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = {"id": "fate_m_999_v001", "family_index": 1, "variant_index": 1,
                      "formal_statement": SOURCE, "sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}
            problems = root / "problems.jsonl"
            problems.write_text(json.dumps(record) + "\n", encoding="utf-8")
            top_hash = sha256_file(problems)
            count = write_sessions(load_records(problems), root / "lean", root / "sessions.jsonl",
                                   SearchOptions(), "http://p", "http://v", top_hash, HASH)
            self.assertEqual(count, 1)
            manifest = json.loads((root / "sessions.jsonl").read_text())
            self.assertEqual(manifest["problems_sha256"], top_hash)


class WallClockTests(unittest.TestCase):
    def _audit(self, value: object):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wall.jsonl"
            dump(path, {"name": "tactic_gen", "extra": {"result": value}})
            return audit_wall_clock(path)

    def test_actual_reap_raw_json_string(self) -> None:
        raw = json.dumps({"choices": [{"message": {"content": "rfl"}}]})
        audit = self._audit(raw)
        self.assertTrue(audit.valid)
        self.assertEqual(audit.policy_events, 1)

    def test_decoded_object_fixture_is_supported(self) -> None:
        self.assertTrue(self._audit({"choices": [{"message": {"content": "rfl"}}]}).valid)

    def test_malformed_string_null_and_wrong_type_fail_closed(self) -> None:
        for value in ("{bad", None, ["bad"], {"choices": []}):
            with self.subTest(value=value):
                self.assertFalse(self._audit(value).valid)


class EvidenceTests(unittest.TestCase):
    def _classify(self, root: Path, **kwargs):
        return classify_evidence(
            "s1", root, returncode=kwargs.get("returncode", 1), timed_out=False,
            source_sha256=HASH, generated_sha256="b" * 64,
            problems_sha256="c" * 64, tree_id="tree1",
            model_sha256=HASH, runtime_receipt_sha256="d" * 64,
            course_manifest_sha256="e" * 64, strict_lake_sha256="f" * 64,
            verifier_id=VERIFIER_ID, verifier_lock_sha256=VERIFIER_LOCK_HASH,
            verifier_public_key_hex=PUBLIC_KEY_HEX,
            strict_replay_receipt=kwargs.get("strict_replay_receipt"),
        )

    def test_exhausted_with_complete_service_chain_is_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record())
            evidence = self._classify(root)
            self.assertEqual(evidence.classification, "model_unsolved")
            self.assertTrue(evidence.negative_reward_eligible)

    def test_any_service_failure_disables_negative_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(), [service_record(), service_record("r2", status="error")])
            evidence = self._classify(root)
            self.assertEqual(evidence.classification, "indeterminate")
            self.assertIsNone(evidence.reward)
            self.assertFalse(evidence.negative_reward_eligible)

    def test_missing_service_receipt_disables_negative_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(), [])
            evidence = self._classify(root)
            self.assertEqual(evidence.classification, "indeterminate")

    def test_forged_solved_result_cannot_be_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved=True, status="solved", proof="exact True.intro"))
            evidence = self._classify(root, returncode=0)
            self.assertEqual(evidence.classification, "infrastructure_error")
            self.assertFalse(evidence.strict_solved)

    def test_string_false_is_rejected_as_bad_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved="false"))
            evidence = self._classify(root)
            self.assertEqual(evidence.classification, "infrastructure_error")
            self.assertEqual(evidence.status, "invalid_result")

    def _bound_replay(self, root: Path, *, session_id: str = "s1") -> Path:
        replay = root / "strict_replay"
        replay.mkdir()
        theorem = replay / "s1.lean"
        stdout, stderr, lake = replay / "stdout.log", replay / "stderr.log", replay / "lake"
        theorem.write_text("theorem ok : True := True.intro\n", encoding="utf-8")
        stdout.write_bytes(b"")
        stderr.write_bytes(b"")
        lake.write_bytes(b"trusted lake")
        proof = "exact True.intro"
        actor = actor_receipt()
        kernel = {
            "schema_version": "fate.strict_replay.v2", "session_id": session_id,
            "source_id": "s1", "strict_solved": True, "returncode": 0, "timed_out": False,
            "source_sha256": HASH, "generated_sha256": "b" * 64,
            "problems_sha256": "c" * 64, "result_sha256": sha256_file(root / "result.json"),
            "raw_tree_sha256": sha256_file(root / "raw_tree.json"),
            "service_receipts_sha256": sha256_file(root / "service_requests.jsonl"),
            "proof_sha256": hashlib.sha256(proof.encode()).hexdigest(),
            "theorem_sha256": sha256_file(theorem), "stdout_sha256": sha256_file(stdout),
            "stderr_sha256": sha256_file(stderr), "forbidden_tokens": [], "diagnostics": [],
            "lean_version": "Lean (version 4.28.0, test)", "lean_githash": "9" * 40,
            "lean_toolchain": "leanprover/lean4:v4.28.0", "lake_manifest_sha256": "e" * 64,
            "lake_executable": str(lake), "lake_executable_sha256": "f" * 64,
            "runtime_receipt_sha256": "d" * 64, "model_sha256": HASH,
            "verifier_id": VERIFIER_ID, "verifier_lock_sha256": VERIFIER_LOCK_HASH,
            "request_sha256": actor["request_sha256"], "statement_sha256": HASH,
            "selected_path_sha256": object_hash(actor["selected_path"]),
            "initial_state_sha256": "3" * 64, "final_state_sha256": "4" * 64,
            "command": [str(lake), "env", "lean", "--json", "-E", "hasSorry", str(theorem)],
            "timeout_seconds": 10,
        }
        # Match the pin used by _classify.
        kernel["lake_executable_sha256"] = sha256_file(lake)
        kernel["kernel_receipt_sha256"] = object_hash(kernel)
        kernel_path = replay / "kernel_receipt.json"
        kernel_path.write_text(json.dumps(kernel), encoding="utf-8")
        key_path = root / "test-verifier.key"
        key_path.write_bytes(bytes(range(32)))
        lock = {"verifier_id": VERIFIER_ID, "ed25519_public_key_hex": PUBLIC_KEY_HEX}
        receipt = sign_actor_receipt(
            actor, verifier_lock=lock, verifier_lock_sha256=VERIFIER_LOCK_HASH,
            request_sha256=actor["request_sha256"], statement_sha256=HASH,
            selected_path_sha256=object_hash(actor["selected_path"]),
            initial_state_sha256="3" * 64, final_state_sha256="4" * 64,
            kernel_receipt_sha256=sha256_file(kernel_path), private_key_path=key_path,
        )
        path = replay / "receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def test_bound_second_process_receipt_admits_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved=True, status="solved", proof="exact True.intro"))
            replay = self._bound_replay(root)
            evidence = classify_evidence(
                "s1", root, returncode=0, timed_out=False, source_sha256=HASH,
                generated_sha256="b" * 64, problems_sha256="c" * 64, tree_id="tree1",
                model_sha256=HASH, runtime_receipt_sha256="d" * 64,
                course_manifest_sha256="e" * 64,
                strict_lake_sha256=sha256_file(replay.parent / "lake"),
                verifier_id=VERIFIER_ID, verifier_lock_sha256=VERIFIER_LOCK_HASH,
                verifier_public_key_hex=PUBLIC_KEY_HEX,
                strict_replay_receipt=replay,
            )
            self.assertEqual(evidence.classification, "strict_success")
            self.assertEqual(evidence.reward, 1.0)

    def test_forged_replay_session_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved=True, status="solved", proof="exact True.intro"))
            replay = self._bound_replay(root, session_id="other")
            evidence = classify_evidence(
                "s1", root, returncode=0, timed_out=False, source_sha256=HASH,
                generated_sha256="b" * 64, problems_sha256="c" * 64, tree_id="tree1",
                model_sha256=HASH, runtime_receipt_sha256="d" * 64,
                course_manifest_sha256="e" * 64,
                strict_lake_sha256=sha256_file(replay.parent / "lake"),
                verifier_id=VERIFIER_ID, verifier_lock_sha256=VERIFIER_LOCK_HASH,
                verifier_public_key_hex=PUBLIC_KEY_HEX,
                strict_replay_receipt=replay,
            )
            self.assertEqual(evidence.classification, "verification_rejected")
            self.assertIsNone(evidence.reward)

    def test_rehashed_but_unsigned_verifier_forgery_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved=True, status="solved", proof="exact True.intro"))
            replay = self._bound_replay(root)
            receipt = json.loads(replay.read_text())
            receipt["verification"]["final_state_sha256"] = "8" * 64
            receipt["verification"]["verification_receipt_sha256"] = object_hash({
                key: value for key, value in receipt["verification"].items()
                if key not in {"verification_receipt_sha256", "signature_hex"}
            })
            receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
            replay.write_text(json.dumps(receipt), encoding="utf-8")
            evidence = classify_evidence(
                "s1", root, returncode=0, timed_out=False, source_sha256=HASH,
                generated_sha256="b" * 64, problems_sha256="c" * 64, tree_id="tree1",
                model_sha256=HASH, runtime_receipt_sha256="d" * 64,
                course_manifest_sha256="e" * 64,
                strict_lake_sha256=sha256_file(replay.parent / "lake"),
                verifier_id=VERIFIER_ID, verifier_lock_sha256=VERIFIER_LOCK_HASH,
                verifier_public_key_hex=PUBLIC_KEY_HEX, strict_replay_receipt=replay,
            )
            self.assertEqual(evidence.classification, "verification_rejected")
            self.assertIn("signature", evidence.reason)

    def test_cost_counter_tamper_is_rejected_even_after_top_level_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_session_dir(root, result_record(solved=True, status="solved", proof="exact True.intro"))
            replay = self._bound_replay(root)
            receipt = json.loads(replay.read_text())
            receipt["cost"]["generated_tokens"] = 0
            receipt["receipt_sha256"] = object_hash(receipt, "receipt_sha256")
            replay.write_text(json.dumps(receipt), encoding="utf-8")
            evidence = classify_evidence(
                "s1", root, returncode=0, timed_out=False, source_sha256=HASH,
                generated_sha256="b" * 64, problems_sha256="c" * 64, tree_id="tree1",
                model_sha256=HASH, runtime_receipt_sha256="d" * 64,
                course_manifest_sha256="e" * 64,
                strict_lake_sha256=sha256_file(replay.parent / "lake"),
                verifier_id=VERIFIER_ID, verifier_lock_sha256=VERIFIER_LOCK_HASH,
                verifier_public_key_hex=PUBLIC_KEY_HEX, strict_replay_receipt=replay,
            )
            self.assertEqual(evidence.classification, "verification_rejected")
            self.assertIn("cost_sha256", evidence.reason)


class StrictReplayTests(unittest.TestCase):
    def test_materialize_enables_fatal_warnings(self) -> None:
        replay = materialize(SOURCE, "exact h")
        self.assertIn("set_option warningAsError true", replay)
        self.assertIn("set_option linter.unusedVariables false", replay)
        self.assertIn("set_option linter.unusedSimpArgs false", replay)
        self.assertIn("by\n  exact h", replay)

    def test_explicit_scanner_rejects_sorry_admit_holes_and_auxiliary_sorry(self) -> None:
        cases = ["sorry", "admit", "exact (by sorry)", "have h : True := by sorry\nexact h", "exact ?_", "simp?"]
        for proof in cases:
            with self.subTest(proof=proof):
                self.assertTrue(scan_forbidden_tokens(proof))
        self.assertEqual(scan_forbidden_tokens("exact True.intro"), [])

    def _inputs(self, root: Path, proof: str) -> ReplayInputs:
        workspace = root / "workspace"
        workspace.mkdir()
        record = {"id": "s1", "family_index": 1, "variant_index": 1,
                  "formal_statement": SOURCE, "sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}
        problems = workspace / "problems.jsonl"
        problems.write_text(json.dumps(record) + "\n", encoding="utf-8")
        dump(workspace / "result.json", result_record(solved=True, status="solved", proof=proof))
        dump(workspace / "raw_tree.json", {"nodes": []})
        dump(workspace / "services.jsonl", service_record())
        dump(workspace / "actor_receipt.json", actor_receipt(record["sha256"]))
        project = workspace / "course"
        project.mkdir()
        (project / "lean-toolchain").write_text("leanprover/lean4:v4.28.0\n", encoding="utf-8")
        dump(project / "lake-manifest.json", {
            "version": "1.1.0", "packages": [{"name": "mathlib", "rev": "a" * 40}],
        })
        lake = workspace / "trusted-lake"
        lake.write_bytes(b"trusted lake shim")
        private_key = root / "verifier-private.key"
        private_key.write_bytes(bytes(range(32)))
        lock = workspace / "verifier-lock.json"
        dump(lock, {
            "schema_version": 1, "verifier_id": VERIFIER_ID,
            "ed25519_public_key_hex": PUBLIC_KEY_HEX,
            "lean_toolchain": "leanprover/lean4:v4.28.0",
            "lean_version": "Lean (version 4.28.0, test)", "lean_git_hash": "d" * 40,
            "mathlib_revision": "a" * 40,
            "executor_source_sha256": sha256_file(Path(run_strict_replay.__code__.co_filename)),
            "executor_binary_sha256": sha256_file(lake),
            "runtime_receipt_sha256": "d" * 64,
            "lake_manifest_sha256": sha256_file(project / "lake-manifest.json"),
            "kernel_command": ["{lake}", "env", "lean", "--json", "-E", "hasSorry", "{theorem}"],
            "tactic_timeout_seconds": 2,
            "forbidden_tokens": ["sorry", "admit", "holes"],
        })
        return ReplayInputs(
            problems=problems, expected_problems_sha256=sha256_file(problems), source_id="s1",
            result_json=workspace / "result.json", raw_tree_json=workspace / "raw_tree.json",
            service_receipts_jsonl=workspace / "services.jsonl",
            actor_receipt_json=workspace / "actor_receipt.json", generated_sha256="b" * 64,
            model_sha256=HASH, runtime_receipt_sha256="d" * 64,
            expected_course_manifest_sha256=sha256_file(project / "lake-manifest.json"),
            expected_lake_sha256=sha256_file(lake), verifier_lock=lock,
            expected_verifier_lock_sha256=sha256_file(lock),
            verifier_private_key=private_key, workspace_root=workspace,
            course_project=project, output_dir=workspace / "replay", lake=str(lake), timeout_seconds=2,
        )

    @staticmethod
    def _fake_run(command, _cwd, _timeout):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, b"Lean (version 4.28.0, test)\n", b"")
        if "-g" in command:
            return subprocess.CompletedProcess(command, 0, ("d" * 40 + "\n").encode(), b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def test_replay_command_is_hardened_and_receipt_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("fate_reap.strict_replay._run", self._fake_run):
            code, receipt_path = run_strict_replay(self._inputs(Path(tmp), "exact True.intro"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(receipt["verification"]["result"], "verified")
            self.assertEqual(len(receipt["verification"]["signature_hex"]), 128)
            kernel = json.loads((receipt_path.parent / "kernel_receipt.json").read_text())
            self.assertTrue(kernel["strict_solved"])
            self.assertIn("--json", kernel["command"])
            self.assertIn("hasSorry", kernel["command"])
            theorem = (receipt_path.parent / "s1.lean").read_text()
            self.assertIn("set_option warningAsError true", theorem)
            self.assertIn("set_option linter.unusedVariables false", theorem)
            self.assertIn("set_option linter.unusedSimpArgs false", theorem)
            self.assertIn("by\n  exact True.intro", theorem)

    def test_has_sorry_diagnostic_is_fail_closed(self) -> None:
        def fake(command, cwd, timeout):
            if "--version" in command or "-g" in command:
                return self._fake_run(command, cwd, timeout)
            diagnostic = {"kind": "hasSorry", "severity": "warning", "message": "declaration uses sorry"}
            return subprocess.CompletedProcess(command, 0, (json.dumps(diagnostic) + "\n").encode(), b"")

        with tempfile.TemporaryDirectory() as tmp, patch("fate_reap.strict_replay._run", fake):
            code, receipt_path = run_strict_replay(self._inputs(Path(tmp), "exact h"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(code, 1)
            self.assertFalse(receipt["strict_solved"])
            self.assertEqual(receipt["diagnostics"][0]["kind"], "hasSorry")

    def test_lean_error_diagnostic_is_fail_closed(self) -> None:
        def fake(command, cwd, timeout):
            if "--version" in command or "-g" in command:
                return self._fake_run(command, cwd, timeout)
            diagnostic = {"severity": "error", "message": "unknown identifier"}
            return subprocess.CompletedProcess(command, 0, (json.dumps(diagnostic) + "\n").encode(), b"")

        with tempfile.TemporaryDirectory() as tmp, patch("fate_reap.strict_replay._run", fake):
            code, receipt_path = run_strict_replay(self._inputs(Path(tmp), "exact h"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(code, 1)
            self.assertFalse(receipt["strict_solved"])
            self.assertEqual(receipt["diagnostics"][0]["severity"], "error")

    def test_sorry_never_launches_compile_and_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("fate_reap.strict_replay._run", self._fake_run):
            code, receipt_path = run_strict_replay(self._inputs(Path(tmp), "exact (by sorry)"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(code, 1)
            self.assertFalse(receipt["strict_solved"])
            self.assertIn("sorry", receipt["forbidden_tokens"])

    def test_replay_timeout_is_fail_closed(self) -> None:
        calls = 0
        def fake(command, cwd, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise subprocess.TimeoutExpired(command, timeout)
            return self._fake_run(command, cwd, timeout)
        with tempfile.TemporaryDirectory() as tmp, patch("fate_reap.strict_replay._run", fake):
            code, receipt_path = run_strict_replay(self._inputs(Path(tmp), "exact h"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(code, 1)
            self.assertTrue(receipt["timed_out"])

    def test_private_key_inside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inputs = self._inputs(Path(tmp), "exact h")
            inside = inputs.workspace_root / "bad.key"
            inside.write_bytes(bytes(range(32)))
            inputs = ReplayInputs(**{**vars(inputs), "verifier_private_key": inside})
            with self.assertRaisesRegex(ValueError, "outside the workspace"):
                run_strict_replay(inputs)

    def test_broken_actor_state_chain_is_rejected_before_lean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inputs = self._inputs(Path(tmp), "exact h")
            actor = json.loads(inputs.actor_receipt_json.read_text())
            actor["selected_path"][0]["state_before_sha256"] = "7" * 64
            actor["request_sha256"] = object_hash(actor["request"])
            inputs.actor_receipt_json.write_text(json.dumps(actor), encoding="utf-8")
            with patch("fate_reap.strict_replay._run") as run:
                with self.assertRaisesRegex(ValueError, "state chain"):
                    run_strict_replay(inputs)
                run.assert_not_called()


class SessionIsolationTests(unittest.TestCase):
    def _session(self, name: str, suffix: str) -> Session:
        return Session(name, Path(f"{name}.lean"), "http://p", "http://v", HASH,
                       suffix * 64, "c" * 64, HASH)

    def test_runner_overrides_shared_observer_and_separates_two_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inherited = {"REAP_OBSERVER_PATH": "/shared/bad", "REAP_CHECKPOINT_DIR": "/shared/bad"}
            envs = []
            for session in (self._session("s1", "1"), self._session("s2", "2")):
                directory = root / session.session_id
                directory.mkdir()
                envs.append(build_session_env(inherited, session, directory, observer_mode="checkpoint",
                                              initial_policy_version=0, checkpoint_timeout_seconds=9))
            self.assertNotEqual(envs[0]["REAP_OBSERVER_PATH"], envs[1]["REAP_OBSERVER_PATH"])
            self.assertNotEqual(envs[0]["REAP_CHECKPOINT_DIR"], envs[1]["REAP_CHECKPOINT_DIR"])
            self.assertNotIn("/shared/bad", envs[0].values())

    def test_concurrent_session_ack_files_do_not_collide_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def create(name: str):
                session = self._session(name, "1" if name == "s1" else "2")
                directory = root / name
                directory.mkdir()
                env = build_session_env({}, session, directory, observer_mode="checkpoint",
                                        initial_policy_version=0, checkpoint_timeout_seconds=9)
                observer = Path(env["REAP_OBSERVER_PATH"])
                records = [
                    {"schema_version": "reap.training.observer.v1", "session_id": name,
                     "tree_id": session.tree_id, "policy_version": 0, "sequence": 0,
                     "kind": "checkpoint", "step": 0},
                    {"schema_version": "reap.training.observer.v1", "session_id": name,
                     "tree_id": session.tree_id, "policy_version": 1, "sequence": 1,
                     "kind": "checkpoint_ack", "step": 0,
                     "previous_policy_version": 0, "next_policy_version": 1},
                ]
                observer.write_text("".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
                checkpoint_dir = Path(env["REAP_CHECKPOINT_DIR"])
                ack = write_checkpoint_ack(checkpoint_dir, session_id=name, tree_id=session.tree_id,
                                           step=0, previous_policy_version=0, policy_version=1,
                                           coordinator_run_id=f"coord-{name}", model_sha256=HASH)
                audit = audit_checkpoints(observer, checkpoint_dir, session_id=name,
                                          tree_id=session.tree_id, initial_policy_version=0)
                return ack, audit
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create, ("s1", "s2")))
            self.assertTrue(all(audit.valid for _, audit in results))
            self.assertNotEqual(results[0][0], results[1][0])

    def test_cross_session_coordinator_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = self._session("s1", "1")
            directory = root / "s1"
            directory.mkdir()
            env = build_session_env({}, session, directory, observer_mode="checkpoint",
                                    initial_policy_version=0, checkpoint_timeout_seconds=9)
            observer = Path(env["REAP_OBSERVER_PATH"])
            observer.write_text("".join(json.dumps(item) + "\n" for item in [
                {"schema_version": "reap.training.observer.v1", "session_id": "s1",
                 "tree_id": session.tree_id, "policy_version": 0, "sequence": 0,
                 "kind": "checkpoint", "step": 0},
                {"schema_version": "reap.training.observer.v1", "session_id": "s1",
                 "tree_id": session.tree_id, "policy_version": 1, "sequence": 1,
                 "kind": "checkpoint_ack", "step": 0,
                 "previous_policy_version": 0, "next_policy_version": 1},
            ]), encoding="utf-8")
            checkpoint_dir = Path(env["REAP_CHECKPOINT_DIR"])
            write_checkpoint_ack(checkpoint_dir, session_id="s1", tree_id=session.tree_id,
                                 step=0, previous_policy_version=0, policy_version=1,
                                 coordinator_run_id="coord-s1", model_sha256=HASH)
            receipt_path = checkpoint_dir / "coordinator_receipts.jsonl"
            receipt = json.loads(receipt_path.read_text())
            receipt["session_id"] = "s2"
            receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            audit = audit_checkpoints(observer, checkpoint_dir, session_id="s1",
                                      tree_id=session.tree_id, initial_policy_version=0)
            self.assertFalse(audit.valid)
            self.assertIn("coordinator receipt binding", audit.reason)


if __name__ == "__main__":
    unittest.main()

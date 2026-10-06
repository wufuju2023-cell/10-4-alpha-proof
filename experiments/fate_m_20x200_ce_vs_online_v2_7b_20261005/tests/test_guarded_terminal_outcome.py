"""Guarded reports must preserve rejection and never invent an Online rerun."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def case(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("guarded_terminal_report_test", scripts / "summarize_guarded_terminal_outcome.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    e = tmp_path / "experiment"
    corrected = e / "runs/formal-20x10-corrected"
    initial = e / "runs/formal-20x10/heldout-eval/initial"
    ce = corrected / "heldout-eval/ce_final"
    for root, label, solved in [(initial, "initial", set(range(24))), (ce, "ce_final", set(range(1, 25)))]:
        tasks = [{"session_id": f"task-{i:02d}", "solved": i in solved} for i in range(40)]
        metrics = {"tasks": 40, "solved_count": 24, "solve_rate": .6,
                   "pass_at_1": .25, "pass_at_2": .5, "pass_at_4": .6,
                   "started_attempts": 106, "generated_tokens": 726,
                   "lean_tactic_executions": 106, "truncated_attempts": 0,
                   "truncation_rate": 0, "timeout_attempts": 0,
                   "by_family": {str(f): {"solve_rate": sum(i in solved for i in [2*f-2, 2*f-1])/2} for f in range(1, 21)}}
        write(root / "report.json", {"state": "DONE", "checkpoint_label": label,
              "settings_fingerprint": "same-frozen-settings", "wall_seconds": 2800,
              "metrics": metrics, "task_results": tasks})
        write(root / "DONE.json", {"state": "DONE", "checkpoint_label": label,
              "report_sha256": digest(root / "report.json")})
    receipt = corrected / "wave_001/online-v2/update/ROLLED_BACK.json"
    write(receipt, {"state": "ROLLED_BACK", "update_receipt": {
          "accepted": False, "reason": "post_step_behavior_kl_limit", "optimizer_steps_total": 0}})
    audit = corrected / "control/online_rollback_identity_audit.json"
    write(audit, {"state": "PASS", "baseline_reuse_allowed": True,
          "online_update_accepted": False, "value_head_tensors_exactly_restored": True,
          "optimizer_and_rng_exactly_restored": True,
          "initial_adapter_model_sha256": "same-policy", "deployed_adapter_model_sha256": "same-policy",
          "pre_policy_sha256": "same-state", "post_policy_sha256": "same-state",
          "receipt_path": str(receipt), "receipt_sha256": digest(receipt),
          "initial_report_sha256": digest(initial / "report.json"),
          "initial_adapter_config_sha256": "config-before", "deployed_adapter_config_sha256": "config-after"})
    checkpoint = corrected / "ce-learner/checkpoint"
    write(checkpoint / "receipt.json", {"samples": 20, "learner_action_tokens": 137})
    write(corrected / "wave_001/ce/update/FORMAL_DONE.wave_001.json", {"checkpoint": str(checkpoint)})
    monkeypatch.setattr(sys, "argv", ["report", "--experiment-root", str(e)])
    return module, e, audit, ce


def test_rejected_update_uses_identity_evidence_without_fabricating_online_run(case):
    module, e, _, _ = case
    module.main()
    report = json.loads((e / "derived/results/corrected_terminal_comparison/comparison.json").read_text())
    assert report["training_updates"]["online_v2"]["accepted"] is False
    assert report["conclusion"]["descriptive_winner"] == "not_applicable_online_update_rejected"
    assert report["execution_scope"]["both_accepted_updates_gate"] is False
    provenance = report["online_result_provenance"]
    assert provenance["independent_heldout_run"] is False
    assert provenance["incremental_heldout_requests"] == 0
    assert provenance["incremental_heldout_gpu_seconds"] == 0
    assert report["checkpoints"]["online_final"]["wall_seconds"] == 0
    assert report["effects"]["paired_ce_vs_online"] == {"left_only": 1, "right_only": 1, "both": 23, "neither": 15}
    assert not (e / "runs/formal-20x10-corrected/heldout-eval/online_final").exists()


def test_policy_mismatch_blocks_identity_reuse_even_when_audit_claims_pass(case):
    module, e, audit, _ = case
    value = json.loads(audit.read_text())
    value["deployed_adapter_model_sha256"] = "different-policy"
    write(audit, value)
    with pytest.raises(AssertionError):
        module.main()
    assert not (e / "derived/results/corrected_terminal_comparison").exists()


def test_partial_ce_evaluation_cannot_be_reported_as_terminal(case):
    module, e, _, ce = case
    value = json.loads((ce / "report.json").read_text())
    value["metrics"]["tasks"] = 39
    write(ce / "report.json", value)
    write(ce / "DONE.json", {"state": "DONE", "checkpoint_label": "ce_final", "report_sha256": digest(ce / "report.json")})
    with pytest.raises(ValueError, match="all 40"):
        module.main()
    assert not (e / "runs/formal-20x10-corrected/control/terminal_outcome_DONE.json").exists()

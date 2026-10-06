from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ce_arm.budgets import normalize_shared_budget


LEGACY = {
    "max_attempts_per_problem": 2,
    "max_generated_tokens_per_problem": 256,
    "max_lean_tactic_executions_per_problem": 64,
}


def _representative_plan() -> dict:
    path = (
        EXPERIMENT / "workstreams" / "policy_service_bridge" / "config"
        / "representative_smoke_plan.frozen.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_current_representative_plan_derives_strict_per_problem_limits():
    assert normalize_shared_budget(_representative_plan()) == {
        "max_attempts_per_problem": 1,
        "max_generated_tokens_per_problem": 64 * 256,
        "max_lean_tactic_executions_per_problem": 64,
    }


def test_original_top_level_format_remains_supported():
    assert normalize_shared_budget(LEGACY) == LEGACY


def test_matching_dual_representation_is_accepted():
    plan = _representative_plan()
    plan.update(normalize_shared_budget(plan))
    assert normalize_shared_budget(plan) == {
        "max_attempts_per_problem": 1,
        "max_generated_tokens_per_problem": 64 * 256,
        "max_lean_tactic_executions_per_problem": 64,
    }


def test_conflicting_dual_representation_fails_closed():
    plan = _representative_plan()
    plan.update(normalize_shared_budget(plan))
    plan["max_generated_tokens_per_problem"] += 1
    with pytest.raises(ValueError, match="disagree"):
        normalize_shared_budget(plan)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda plan: plan["protocol"]["generation"].update(n=True), "positive integer"),
        (
            lambda plan: plan["protocol"]["planned_cost_ceiling"].update(
                generated_tokens=1
            ),
            "must equal",
        ),
        (lambda plan: plan["tasks"].append(plan["tasks"][0]), "duplicate"),
        (lambda plan: plan.update(schema_version="unknown"), "unsupported"),
    ],
)
def test_representative_plan_rejects_ambiguous_or_incoherent_budget(mutation, message):
    plan = _representative_plan()
    mutation(plan)
    with pytest.raises(ValueError, match=message):
        normalize_shared_budget(plan)


def test_partial_legacy_format_fails_closed():
    with pytest.raises(ValueError, match="incomplete"):
        normalize_shared_budget({"max_attempts_per_problem": 1})

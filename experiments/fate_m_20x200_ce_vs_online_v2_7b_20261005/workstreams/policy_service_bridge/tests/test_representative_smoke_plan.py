from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


BRIDGE = Path(__file__).resolve().parents[1]
PLAN = BRIDGE / "config" / "representative_smoke_plan.frozen.json"
CAPTURE_SCRIPT = BRIDGE / "scripts" / "capture_representative_reap_prompts.py"
SPEC = importlib.util.spec_from_file_location("representative_plan_paths", CAPTURE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
PROBLEMS = capture.resolve_problems_path(PLAN)
VARIANTS = (1, 40, 80, 120, 160, 175)


def test_frozen_representative_tasks_match_authoritative_jsonl() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    assert plan["source"]["problems_relative_path"] == "../../../data/problems.jsonl"
    source_bytes = capture.read_problems_bytes(PROBLEMS)
    assert hashlib.sha256(source_bytes).hexdigest() == plan["source"]["problems_sha256"]
    records = [json.loads(line) for line in source_bytes.decode("utf-8").splitlines() if line]
    assert len(records) == plan["source"]["required_record_count"] == 4000
    by_id = {record["id"]: record for record in records}
    assert len(by_id) == len(records)

    expected_order = [
        session_id
        for variant in VARIANTS
        for session_id in (f"fate_m_003_v{variant:03d}", f"fate_m_076_v{variant:03d}")
    ]
    assert [task["session_id"] for task in plan["tasks"]] == expected_order
    assert {(task["family_index"], task["variant_index"]) for task in plan["tasks"]} == {
        (family, variant) for family in (1, 20) for variant in VARIANTS
    }
    for task in plan["tasks"]:
        record = by_id[task["session_id"]]
        assert record["family_index"] == task["family_index"]
        assert record["variant_index"] == task["variant_index"]
        assert record["sha256"] == task["formal_statement_sha256"]
        assert hashlib.sha256(record["formal_statement"].encode()).hexdigest() == record["sha256"]


def test_representative_budget_and_safety_contract_is_frozen() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    protocol = plan["protocol"]
    execution = protocol["execution"]
    ceiling = protocol["planned_cost_ceiling"]
    assert protocol["generation"] == {
        "temperature": 1.5,
        "top_p": 0.9,
        "max_tokens": 256,
        "n": 64,
        "logprobs": True,
    }
    assert protocol["search"] == {"max_steps": 1, "max_goals": 64, "num_premises": 0}
    assert execution["model_load_count"] == execution["formal_r16_adapter_load_count"] == 1
    assert execution["session_concurrency"] == 1
    assert execution["output_must_be_below"] == "/tmp"
    assert execution["training"] is False
    assert execution["integration_bundle"] is False
    assert execution["heartbeat_interval_seconds"] <= 30
    assert ceiling == {
        "policy_requests": len(plan["tasks"]),
        "returned_candidates": len(plan["tasks"]) * 64,
        "generated_tokens": len(plan["tasks"]) * 64 * 256,
    }
    prompt = plan["prompt_contract"]
    assert prompt["synthetic_root_state_forbidden"] is True
    assert prompt["per_session_root_state_sha256_required_before_gpu_launch"] is True
    assert prompt["root_state_pins"] is None
    assert plan["status"].endswith("prompt_state_pins_pending")

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


BRIDGE = Path(__file__).resolve().parents[1]
SCRIPT = BRIDGE / "scripts" / "capture_representative_reap_prompts.py"
PLAN = BRIDGE / "config" / "representative_smoke_plan.frozen.json"
WAVE001_PLAN = BRIDGE / "config" / "wave001_20family_plan.frozen.json"
SPEC = importlib.util.spec_from_file_location("capture_representative_reap_prompts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
PROBLEMS = capture.resolve_problems_path(PLAN)


def reap_prompt(state: str) -> str:
    return (
        "User: Please generate a tactic in lean4 to solve the state.\n"
        "Here're some theorems that may be helpful:\n\nSTATE:\n"
        f"{state}\nTACTIC:\n\nAssistant:"
    )


def request(prompt: str) -> bytes:
    return json.dumps(
        {
            "model": "REAL-Prover",
            "messages": [{"role": "user", "content": prompt}],
            "n": 64,
            "temperature": 1.5,
            "max_tokens": 256,
            "logprobs": True,
        },
        separators=(",", ":"),
    ).encode()


def test_cpu_capture_uses_exact_frozen_tasks_and_one_expansion() -> None:
    plan, tasks = capture.load_plan_and_tasks(PLAN, PROBLEMS)
    assert len(tasks) == 12
    assert [row["id"] for row in tasks] == [task["session_id"] for task in plan["tasks"]]
    options = capture.search_options()
    assert options.num_samples == 64
    assert options.max_tokens == 256
    assert options.max_steps == 1
    assert options.num_premises == 0


def test_wave001_plan_selects_variant_one_from_all_twenty_families() -> None:
    plan, tasks = capture.load_plan_and_tasks(WAVE001_PLAN, PROBLEMS)
    assert plan["expected_session_count"] == len(tasks) == 20
    assert [row["family_index"] for row in tasks] == list(range(1, 21))
    assert {row["variant_index"] for row in tasks} == {1}


def test_plan_relative_problems_path_has_priority(tmp_path: Path) -> None:
    data = tmp_path / "data" / "problems.jsonl"
    data.parent.mkdir()
    data.write_text("fixture\n", encoding="utf-8")
    config = tmp_path / "nested" / "config"
    config.mkdir(parents=True)
    plan = config / "plan.json"
    plan.write_text(
        json.dumps({"source": {"problems_relative_path": "../../data/problems.jsonl"}}),
        encoding="utf-8",
    )
    assert capture.resolve_problems_path(plan) == data.resolve()
    explicit = tmp_path / "explicit.jsonl"
    explicit.write_text("explicit\n", encoding="utf-8")
    assert capture.resolve_problems_path(plan, explicit) == explicit.resolve()


def test_plan_relative_gzip_is_a_lossless_repository_fallback(tmp_path: Path) -> None:
    source = b'{"id":"fixture"}\n'
    data = tmp_path / "data" / "problems.jsonl.gz"
    data.parent.mkdir()
    data.write_bytes(gzip.compress(source, mtime=0))
    config = tmp_path / "nested" / "config"
    config.mkdir(parents=True)
    plan = config / "plan.json"
    plan.write_text(
        json.dumps({"source": {"problems_relative_path": "../../data/problems.jsonl"}}),
        encoding="utf-8",
    )
    resolved = capture.resolve_problems_path(plan)
    assert resolved == data.resolve()
    assert capture.read_problems_bytes(resolved) == source


def test_capture_records_actual_reap_prompt_and_controlled_provenance(tmp_path: Path) -> None:
    _, tasks = capture.load_plan_and_tasks(PLAN, PROBLEMS)
    task = tasks[0]
    store = capture.CaptureStore(tmp_path, [task])
    state = "x : Nat\nh : x = x\n⊢ x = x"
    prompt = reap_prompt(state)
    response, path = store.capture_policy(task["id"], request(prompt))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["incoming_prompt"] == prompt
    assert saved["root_state"] == state
    assert saved["root_state_sha256"] == hashlib.sha256(state.encode()).hexdigest()
    assert saved["model_loaded"] is False
    assert saved["training"] is False
    assert len(response["choices"]) == 64
    for index, choice in enumerate(response["choices"]):
        assert choice["index"] == choice["raw_sample_index"] == index
        assert choice["message"]["content"] == "skip"
        assert choice["logprobs"]["content"][0]["token"] == "skip"
        assert len(choice["service_candidate_sha256"]) == 64
    with pytest.raises(ValueError, match="duplicate"):
        store.capture_policy(task["id"], request(prompt))


def test_capture_fails_before_writing_for_noncanonical_or_wrong_session(tmp_path: Path) -> None:
    _, tasks = capture.load_plan_and_tasks(PLAN, PROBLEMS)
    store = capture.CaptureStore(tmp_path, [tasks[0]])
    with pytest.raises(ValueError, match="outside the frozen"):
        store.capture_policy("not-frozen", request(reap_prompt("⊢ True")))
    with pytest.raises(ValueError, match="pinned tactic envelope"):
        store.capture_policy(tasks[0]["id"], request("synthetic state"))
    assert not list(tmp_path.rglob("prompt_capture.json"))


def test_pins_manifest_requires_every_capture_and_preserves_root_text(tmp_path: Path) -> None:
    _, all_tasks = capture.load_plan_and_tasks(PLAN, PROBLEMS)
    tasks = all_tasks[:1]
    store = capture.CaptureStore(tmp_path, tasks)
    state = "α : Type\n⊢ True"
    store.capture_policy(tasks[0]["id"], request(reap_prompt(state)))
    process = {
        "session_id": tasks[0]["id"],
        "returncode": 1,
        "timed_out": False,
        "elapsed_seconds": 0.1,
        "stdout_sha256": "a" * 64,
        "stderr_sha256": "b" * 64,
        "observer_sha256": None,
        "result_sha256": None,
    }
    lake = tmp_path / "lake"
    lake.write_bytes(b"lake")
    reap = tmp_path / "reap"
    reap.mkdir()
    manifest = capture.build_pins_manifest(
        plan_path=PLAN,
        problems_path=PROBLEMS,
        tasks=tasks,
        store=store,
        process_results=[process],
        reap_project=reap,
        lake=lake,
    )
    assert manifest["gpu_used"] is False
    assert manifest["model_loaded"] is False
    assert manifest["pins"][0]["root_state"] == state
    assert manifest["pins"][0]["lean_process"]["returncode"] == 1
    with pytest.raises(ValueError, match="not all frozen"):
        capture.build_pins_manifest(
            plan_path=PLAN,
            problems_path=PROBLEMS,
            tasks=all_tasks[:2],
            store=store,
            process_results=[process],
            reap_project=reap,
            lake=lake,
        )


def test_capture_entrypoint_has_no_gpu_or_model_runtime_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("import torch", "from torch", "transformers", "from peft", "PeftModel"):
        assert forbidden not in source
    assert "build_root_state" not in source
    assert "parse_reap_tactic_state(incoming_prompt)" in source

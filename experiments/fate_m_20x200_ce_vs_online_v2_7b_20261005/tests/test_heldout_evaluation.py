from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import threading


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_rule_and_candidate_rendering_are_frozen():
    module = load_script("evaluate_heldout_checkpoint.py")
    row = {"variant_index": 9, "family_index": 1}
    assert [module.attempt_seed(row, index) for index in range(4)] == [
        20470015, 20470016, 20470017, 20470018
    ]
    source = "import Mathlib\n\ntheorem demo : True := by\n  sorry\n"
    rendered = module.render_candidate_source(source, "exact True.intro")
    assert "sorry" not in rendered
    assert rendered.endswith("  exact True.intro\n")


def test_model_transaction_is_a_factory_returning_the_same_lock():
    module = load_script("evaluate_heldout_checkpoint.py")
    transaction = threading.Lock()
    factory = module.model_transaction_factory(transaction)
    assert callable(factory)
    assert factory() is transaction
    with factory():
        assert transaction.locked()


def test_checkpoint_summary_uses_adaptive_prefix_pass_at_k():
    module = load_script("evaluate_heldout_checkpoint.py")
    tasks = []
    for index in range(40):
        first = 1 if index < 10 else 2 if index < 20 else 4 if index < 30 else None
        attempts = []
        for attempt in range(1, (first or 4) + 1):
            attempts.append({
                "strict_success": attempt == first,
                "truncated": False,
                "lean_status": "verified" if attempt == first else "invalid",
                "generated_tokens": 3,
                "lean_tactic_executions": 1,
                "strict_lean_checks": 1,
                "generation_wall_seconds": 0.1,
                "generation_gpu_seconds": 0.1,
                "lean_elapsed_seconds": 0.2,
            })
        tasks.append({
            "session_id": f"task-{index}",
            "family_index": index % 20 + 1,
            "variant_index": 9 if index < 20 else 10,
            "solved": first is not None,
            "first_success_attempt": first,
            "attempts": attempts,
        })
    metrics = module.summarize_tasks(tasks)
    assert metrics["solved_count"] == 30
    assert metrics["pass_at_1"] == 0.25
    assert metrics["pass_at_2"] == 0.5
    assert metrics["pass_at_4"] == 0.75
    assert metrics["truncation_rate"] == 0.0


def test_three_checkpoint_summary_is_paired_and_descriptive():
    module = load_script("summarize_heldout_comparison.py")

    def report(label, solved):
        task_results = [
            {"session_id": f"task-{index}", "solved": index < solved}
            for index in range(40)
        ]
        by_family = {str(index): {"solve_rate": 1.0 if index <= solved // 2 else 0.0}
                     for index in range(1, 21)}
        return {
            "settings_fingerprint": "a" * 64,
            "wall_seconds": 60.0,
            "task_results": task_results,
            "metrics": {
                "solved_count": solved, "tasks": 40, "solve_rate": solved / 40,
                "pass_at_1": solved / 80, "pass_at_2": solved / 60,
                "pass_at_4": solved / 40, "started_attempts": 100,
                "generated_tokens": 300, "lean_tactic_executions": 100,
                "truncated_attempts": 0, "truncation_rate": 0.0,
                "timeout_attempts": 0, "by_family": by_family,
            },
            "checkpoint_label": label,
        }

    comparison = module.summarize({
        "initial": report("initial", 10),
        "ce_final": report("ce_final", 14),
        "online_final": report("online_final", 12),
    })
    assert comparison["effects"]["ce_minus_initial_solved"] == 4
    assert comparison["effects"]["ce_minus_online_solved"] == 2
    assert comparison["effects"]["paired_ce_vs_online"]["left_only"] == 2
    assert comparison["conclusion"]["descriptive_winner"] == "ce_final"


def test_adapter_resolution_accepts_ce_and_online_run_roots(tmp_path):
    module = load_script("evaluate_heldout_checkpoint.py")
    ce_checkpoint = tmp_path / "ce-checkpoint"
    ce_adapter = ce_checkpoint / "adapter"
    ce_adapter.mkdir(parents=True)
    (ce_adapter / "adapter_model.safetensors").write_bytes(b"weights")
    (ce_adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    ce_run = tmp_path / "ce-run"
    ce_run.mkdir()
    (ce_run / "latest.json").write_text(
        json.dumps({"checkpoint": str(ce_checkpoint)}), encoding="utf-8"
    )
    assert module.resolve_adapter(ce_run) == ce_adapter.resolve()

    ce_wave_root = tmp_path / "wave_001" / "ce"
    ce_update = ce_wave_root / "update"
    ce_update.mkdir(parents=True)
    (ce_update / "FORMAL_DONE.wave_001.json").write_text(
        json.dumps({"checkpoint": str(ce_checkpoint)}), encoding="utf-8"
    )
    assert module.resolve_adapter(ce_wave_root) == ce_adapter.resolve()
    assert module.resolve_adapter(ce_update) == ce_adapter.resolve()

    online_checkpoint = tmp_path / "online-checkpoint"
    online_adapter = online_checkpoint / "adapter"
    online_adapter.mkdir(parents=True)
    (online_adapter / "adapter_model.safetensors").write_bytes(b"weights")
    (online_adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    online_run = tmp_path / "online-run"
    online_run.mkdir()
    (online_run / "DONE.json").write_text(
        json.dumps({"checkpoint": {"path": str(online_checkpoint)}}), encoding="utf-8"
    )
    assert module.resolve_adapter(online_run) == online_adapter.resolve()

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


BRIDGE = Path(__file__).resolve().parents[1]
SCRIPT = BRIDGE / "scripts" / "real_reap_wave_one_step.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("real_reap_wave_one_step", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rollout)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_arbitrary_twenty_problem_wave_is_bound_and_formal_budget_is_frozen(
    tmp_path: Path,
) -> None:
    records = []
    tasks = []
    pins = []
    for family in range(1, 21):
        variant = (family % 8) + 1
        session_id = f"fixture_{family:02d}_v{variant:03d}"
        statement = f"import Mathlib\n\ntheorem t{family} : True := by\n  sorry\n"
        statement_sha = _sha(statement)
        records.append({
            "id": session_id,
            "family_index": family,
            "variant_index": variant,
            "formal_statement": statement,
            "sha256": statement_sha,
        })
        tasks.append({
            "session_id": session_id,
            "family_index": family,
            "variant_index": variant,
            "formal_statement_sha256": statement_sha,
        })
        pins.append({
            "session_id": session_id,
            "family_index": family,
            "variant_index": variant,
            "formal_statement_sha256": statement_sha,
            "root_state_sha256": "a" * 64,
        })
    problems = tmp_path / "problems.jsonl"
    problems.write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "expected_session_count": 20,
        "source": {"problems_sha256": rollout.sha256_file(problems)},
        "tasks": tasks,
    }), encoding="utf-8")
    root_pins = tmp_path / "root_state_pins.json"
    root_pins.write_text(json.dumps({
        "plan_sha256": rollout.sha256_file(plan),
        "problems_sha256": rollout.sha256_file(problems),
        "pins": pins,
    }), encoding="utf-8")

    _, selected, selected_pins, problems_sha = rollout.load_wave(
        plan, problems, root_pins
    )

    assert len(selected) == len(selected_pins) == rollout.SESSION_COUNT == 20
    assert {row["variant_index"] for row in selected} != {1}
    assert problems_sha == rollout.sha256_file(problems)
    assert rollout.MAX_ATTEMPTS == 4
    assert rollout.GENERATION.max_new_tokens == 512
    assert rollout.GENERATION.num_return_sequences == 64

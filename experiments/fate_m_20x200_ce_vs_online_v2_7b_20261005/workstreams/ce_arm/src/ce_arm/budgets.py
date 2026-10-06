"""Strict normalization for the two frozen shared-budget representations."""

from __future__ import annotations


_LIMIT_FIELDS = (
    "max_attempts_per_problem",
    "max_generated_tokens_per_problem",
    "max_lean_tactic_executions_per_problem",
)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"shared budget {label} must be a positive integer")
    return value


def _legacy_limits(budget: dict) -> dict[str, int] | None:
    present = [field in budget for field in _LIMIT_FIELDS]
    if not any(present):
        return None
    if not all(present):
        missing = [field for field, exists in zip(_LIMIT_FIELDS, present) if not exists]
        raise ValueError(f"shared budget has incomplete top-level limits: {missing}")
    return {field: _positive_int(budget[field], field) for field in _LIMIT_FIELDS}


def _representative_plan_limits(budget: dict) -> dict[str, int] | None:
    if "protocol" not in budget:
        return None
    if budget.get("schema_version") != "fate.policy_service.representative_smoke_plan.v1":
        raise ValueError("unsupported shared budget representative-plan schema")
    protocol = budget["protocol"]
    if not isinstance(protocol, dict):
        raise ValueError("shared budget protocol must be a JSON object")
    generation = protocol.get("generation")
    search = protocol.get("search")
    ceiling = protocol.get("planned_cost_ceiling")
    if not isinstance(generation, dict):
        raise ValueError("shared budget protocol.generation must be a JSON object")
    if not isinstance(search, dict):
        raise ValueError("shared budget protocol.search must be a JSON object")
    if not isinstance(ceiling, dict):
        raise ValueError(
            "shared budget protocol.planned_cost_ceiling must be a JSON object"
        )

    samples = _positive_int(generation.get("n"), "protocol.generation.n")
    max_tokens = _positive_int(
        generation.get("max_tokens"), "protocol.generation.max_tokens"
    )
    max_steps = _positive_int(search.get("max_steps"), "protocol.search.max_steps")
    max_goals = _positive_int(search.get("max_goals"), "protocol.search.max_goals")

    tasks = budget.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("shared budget representative plan must contain non-empty tasks")
    session_ids: list[str] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"shared budget tasks[{index}] must be a JSON object")
        session_id = task.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError(f"shared budget tasks[{index}].session_id must be non-empty")
        session_ids.append(session_id)
    if len(set(session_ids)) != len(session_ids):
        raise ValueError("shared budget representative plan has duplicate session_id values")

    task_count = len(tasks)
    expected_ceiling = {
        "policy_requests": task_count,
        "returned_candidates": task_count * samples,
        "generated_tokens": task_count * samples * max_tokens,
    }
    for field, expected in expected_ceiling.items():
        observed = _positive_int(
            ceiling.get(field), f"protocol.planned_cost_ceiling.{field}"
        )
        if observed != expected:
            raise ValueError(
                "shared budget protocol.planned_cost_ceiling."
                f"{field} must equal {expected}, got {observed}"
            )

    return {
        "max_attempts_per_problem": max_steps,
        "max_generated_tokens_per_problem": samples * max_tokens,
        "max_lean_tactic_executions_per_problem": max_goals,
    }


def normalize_shared_budget(budget: object) -> dict[str, int]:
    """Return canonical per-problem ceilings without inventing missing limits.

    The original CE lock format declares the three canonical fields at the
    top level.  The signed representative-smoke plan declares the same limits
    through its protocol: max_steps, n*max_tokens, and max_goals respectively.
    If both representations are present they must agree exactly.
    """
    if not isinstance(budget, dict):
        raise ValueError("shared budget must be a JSON object")
    legacy = _legacy_limits(budget)
    representative = _representative_plan_limits(budget)
    if legacy is None and representative is None:
        raise ValueError(
            "shared budget must declare either complete top-level limits or "
            "a representative protocol"
        )
    if legacy is not None and representative is not None and legacy != representative:
        raise ValueError("shared budget top-level and representative limits disagree")
    return legacy if legacy is not None else representative  # type: ignore[return-value]

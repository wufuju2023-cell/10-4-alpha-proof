"""Strict parsing for the pinned Reap tactic-generation prompt envelope."""

from __future__ import annotations


REAP_TACTIC_PROMPT_PREFIX = (
    "User: Please generate a tactic in lean4 to solve the state.\n"
    "Here're some theorems that may be helpful:\n\n"
    "STATE:\n"
)
REAP_TACTIC_PROMPT_SUFFIX = "\nTACTIC:\n\nAssistant:"


def parse_reap_tactic_state(prompt: str) -> str:
    """Extract one Lean state only when the entire Reap envelope is canonical."""
    if type(prompt) is not str or not prompt:
        raise ValueError("Reap prompt must be a non-empty string")
    if "\r" in prompt or not prompt.startswith(REAP_TACTIC_PROMPT_PREFIX) or not prompt.endswith(
            REAP_TACTIC_PROMPT_SUFFIX):
        raise ValueError("Reap prompt does not match the pinned tactic envelope")
    if (prompt.count(REAP_TACTIC_PROMPT_PREFIX) != 1
            or prompt.count(REAP_TACTIC_PROMPT_SUFFIX) != 1):
        raise ValueError("Reap prompt envelope is ambiguous")
    state = prompt[len(REAP_TACTIC_PROMPT_PREFIX):-len(REAP_TACTIC_PROMPT_SUFFIX)]
    if not state or state != state.strip("\n"):
        raise ValueError("Reap prompt contains an empty or padded state")
    goal_lines = [line for line in state.splitlines() if line.startswith("⊢ ")]
    if len(goal_lines) != 1 or state.splitlines()[-1] != goal_lines[0]:
        raise ValueError("Reap root state must end in exactly one main goal")
    return state

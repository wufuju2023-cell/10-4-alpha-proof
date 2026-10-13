from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Callable, Iterable, Protocol
from urllib import request

from .schema import DistillationExample


@dataclass(frozen=True)
class TeacherConfig:
    endpoint: str
    model: str = "deepseek-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.6
    top_p: float = 0.95
    max_tokens: int = 256
    seed: int = 0
    timeout_seconds: float = 60.0
    max_action_chars: int = 512

    def validate(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("teacher endpoint must be HTTP(S)")
        if not self.model or not self.api_key_env:
            raise ValueError("teacher model and api_key_env are required")
        if self.max_tokens < 1 or self.timeout_seconds <= 0 or self.max_action_chars < 1:
            raise ValueError("teacher limits must be positive")


@dataclass(frozen=True)
class VerificationDecision:
    lean_accepted: bool
    on_verified_solution_path: bool
    proof_id: str = ""
    diagnostic: str = ""


class Teacher(Protocol):
    def propose(self, state: str, count: int) -> list[str]: ...


Transport = Callable[[dict[str, object], dict[str, str], float], dict[str, object]]


def _default_transport(
    payload: dict[str, object], headers: dict[str, str], timeout: float
) -> dict[str, object]:
    wire_payload = dict(payload)
    endpoint = str(wire_payload.pop("_endpoint"))
    encoded = json.dumps(wire_payload).encode("utf-8")
    req = request.Request(
        endpoint, data=encoded, headers=headers, method="POST"
    )
    with request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("teacher response must be a JSON object")
    return body


class OpenAICompatibleTeacher:
    """Text-action teacher; no cross-tokenizer logit assumptions are made."""

    def __init__(self, config: TeacherConfig, transport: Transport | None = None) -> None:
        config.validate()
        self.config = config
        self.transport = transport or _default_transport

    def propose(self, state: str, count: int) -> list[str]:
        if not state.strip() or count < 1:
            raise ValueError("state must be non-empty and count positive")
        key = os.environ.get(self.config.api_key_env, "")
        if not key and self.transport is _default_transport:
            raise RuntimeError(
                f"missing teacher credential in environment variable {self.config.api_key_env}"
            )
        system = (
            "You propose Lean 4 tactics for one tactic state. Return only a JSON array of "
            f"exactly {count} tactic strings. Do not include explanations, markdown, `sorry`, "
            "`admit`, or declarations."
        )
        payload: dict[str, object] = {
            "_endpoint": self.config.endpoint,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
            "seed": self.config.seed,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": state},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = self.transport(payload, headers, self.config.timeout_seconds)
        try:
            content = body["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("teacher response lacks choices[0].message.content") from exc
        return self._parse_actions(str(content), count)

    def _parse_actions(self, content: str, count: int) -> list[str]:
        stripped = content.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
        if fence:
            stripped = fence.group(1)
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("teacher content is not a JSON array") from exc
        if not isinstance(parsed, list):
            raise ValueError("teacher content must be a JSON array")
        actions: list[str] = []
        forbidden = re.compile(r"\b(sorry|admit)\b", flags=re.IGNORECASE)
        for item in parsed:
            if not isinstance(item, str):
                continue
            action = item.strip()
            if (
                not action
                or len(action) > self.config.max_action_chars
                or forbidden.search(action)
                or action in actions
            ):
                continue
            actions.append(action)
            if len(actions) == count:
                break
        return actions


def collect_verified_distillation(
    *,
    problem_id: str,
    state_id: str,
    state: str,
    teacher: Teacher,
    verifier: Callable[[str, str], VerificationDecision],
    teacher_candidates: int,
    student_candidates: Iterable[str] = (),
) -> tuple[list[DistillationExample], list[dict[str, object]]]:
    """Union teacher/student actions, preserve every receipt, train only proof-path actions."""

    proposed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, actions in (
        ("student", list(student_candidates)),
        ("teacher", teacher.propose(state, teacher_candidates)),
    ):
        for raw in actions:
            action = raw.strip()
            if action and action not in seen:
                proposed.append((source, action))
                seen.add(action)

    examples: list[DistillationExample] = []
    receipts: list[dict[str, object]] = []
    for source, action in proposed:
        decision = verifier(state, action)
        accepted_for_ce = bool(
            decision.lean_accepted
            and decision.on_verified_solution_path
            and decision.proof_id.strip()
        )
        receipts.append(
            {
                "problem_id": problem_id,
                "state_id": state_id,
                "source": source,
                "action": action,
                "lean_accepted": decision.lean_accepted,
                "on_verified_solution_path": decision.on_verified_solution_path,
                "proof_id": decision.proof_id,
                "diagnostic": decision.diagnostic,
                "accepted_for_ce": accepted_for_ce,
            }
        )
        if accepted_for_ce:
            example = DistillationExample(
                problem_id=problem_id,
                state_id=state_id,
                state=state,
                action=action,
                source=source,
                proof_id=decision.proof_id,
            )
            example.validate()
            examples.append(example)
    return examples, receipts

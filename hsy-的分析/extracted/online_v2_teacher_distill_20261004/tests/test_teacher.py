from __future__ import annotations

from alphaproof_online_v2.teacher import (
    OpenAICompatibleTeacher,
    TeacherConfig,
    VerificationDecision,
    collect_verified_distillation,
)


def test_teacher_output_is_sanitized_and_only_verified_path_enters_ce() -> None:
    def transport(payload, headers, timeout):
        assert payload["model"] == "deepseek-flash"
        assert timeout == 3.0
        assert headers["Content-Type"] == "application/json"
        return {
            "choices": [
                {
                    "message": {
                        "content": '["exact h", "sorry", "exact h", "simp"]'
                    }
                }
            ]
        }

    teacher = OpenAICompatibleTeacher(
        TeacherConfig(endpoint="https://teacher.invalid/v1/chat/completions", timeout_seconds=3.0),
        transport=transport,
    )

    def verifier(state: str, action: str) -> VerificationDecision:
        assert state == "h : P\n⊢ P"
        if action == "exact h":
            return VerificationDecision(True, True, "proof-1")
        return VerificationDecision(True, False, diagnostic="valid step but no verified completion")

    examples, receipts = collect_verified_distillation(
        problem_id="p1",
        state_id="s1",
        state="h : P\n⊢ P",
        teacher=teacher,
        verifier=verifier,
        teacher_candidates=4,
        student_candidates=["assumption"],
    )
    assert [example.action for example in examples] == ["exact h"]
    assert len(receipts) == 3
    assert any(row["action"] == "simp" and not row["accepted_for_ce"] for row in receipts)

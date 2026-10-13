import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_011_v005 {R : Type*} [Ring R] (a b c : R)
    (curriculum_target : (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

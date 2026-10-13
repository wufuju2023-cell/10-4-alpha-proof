import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 2/200; tier 1: direct_target. -/
theorem fate_m_011_v002 {R : Type*} [Ring R] (a b c : R)
    (curriculum_target : (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))
    (curriculum_context_001 : ((100201 : Nat) = 100201)) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

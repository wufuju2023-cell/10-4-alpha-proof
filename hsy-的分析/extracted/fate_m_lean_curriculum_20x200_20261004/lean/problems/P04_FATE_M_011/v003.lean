import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 3/200; tier 1: direct_target. -/
theorem fate_m_011_v003 {R : Type*} [Ring R] (a b c : R)
    (curriculum_target : (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))
    (curriculum_context_001 : ((100301 : Nat) = 100301))
    (curriculum_context_002 : ((100302 : Nat) = 100302)) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

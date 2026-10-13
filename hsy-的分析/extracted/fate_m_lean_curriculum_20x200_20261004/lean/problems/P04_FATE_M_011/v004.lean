import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_011_v004 {R : Type*} [Ring R] (a b c : R)
    (curriculum_target : (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

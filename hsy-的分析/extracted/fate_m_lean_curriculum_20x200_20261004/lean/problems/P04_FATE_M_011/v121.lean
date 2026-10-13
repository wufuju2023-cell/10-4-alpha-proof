import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_011_v121 {R : Type*} [Ring R] (a b c : R)
    (curriculum_cases : ((a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

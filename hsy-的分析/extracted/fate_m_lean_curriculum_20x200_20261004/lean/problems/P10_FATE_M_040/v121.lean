import Mathlib

namespace FateCurriculum

/-- FATE-M #40, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_040_v121 (curriculum_cases : ((∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) := by
  sorry

end FateCurriculum

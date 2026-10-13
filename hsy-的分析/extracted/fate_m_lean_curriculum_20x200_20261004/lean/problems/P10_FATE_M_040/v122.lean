import Mathlib

namespace FateCurriculum

/-- FATE-M #40, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_040_v122 (curriculum_cases : ((∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    (∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) := by
  sorry

end FateCurriculum

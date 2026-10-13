import Mathlib

namespace FateCurriculum

/-- FATE-M #40, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_040_v123 (curriculum_cases : ((∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) := by
  sorry

end FateCurriculum

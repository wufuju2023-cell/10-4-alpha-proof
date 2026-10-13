import Mathlib

namespace FateCurriculum

/-- FATE-M #40, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_040_v124 (curriculum_cases : ((∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (∀ z : ℤ, ∃ a b : ℤ, z = a * 5 + b * 7) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v122 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    (a = 0) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v124 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (a = 0) := by
  sorry

end FateCurriculum

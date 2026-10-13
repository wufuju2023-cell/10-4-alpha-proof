import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v123 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (a = 0) := by
  sorry

end FateCurriculum

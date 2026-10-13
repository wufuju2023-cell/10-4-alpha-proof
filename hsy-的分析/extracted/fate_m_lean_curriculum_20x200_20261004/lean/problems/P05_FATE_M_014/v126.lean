import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 126/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v126 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((412601 : Nat) = 412601) ∧ False) ∨ ((((412602 : Nat) = 412602) ∧ False) ∨ ((((412603 : Nat) = 412603) ∧ False) ∨ ((((412604 : Nat) = 412604) ∧ False) ∨ ((((412605 : Nat) = 412605) ∧ False) ∨ (((412606 : Nat) = 412606) ∧ False)))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

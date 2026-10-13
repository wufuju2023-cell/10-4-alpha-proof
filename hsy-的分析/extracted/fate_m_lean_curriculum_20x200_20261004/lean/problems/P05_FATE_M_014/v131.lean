import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 131/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v131 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((413101 : Nat) = 413101) ∧ False) ∨ ((((413102 : Nat) = 413102) ∧ False) ∨ ((((413103 : Nat) = 413103) ∧ False) ∨ ((((413104 : Nat) = 413104) ∧ False) ∨ ((((413105 : Nat) = 413105) ∧ False) ∨ ((((413106 : Nat) = 413106) ∧ False) ∨ ((((413107 : Nat) = 413107) ∧ False) ∨ ((((413108 : Nat) = 413108) ∧ False) ∨ ((((413109 : Nat) = 413109) ∧ False) ∨ ((((413110 : Nat) = 413110) ∧ False) ∨ (((413111 : Nat) = 413111) ∧ False))))))))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 126/200; tier 4: disjunction_elimination. -/
theorem fate_m_011_v126 {R : Type*} [Ring R] (a b c : R)
    (curriculum_cases : ((a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) ∨ ((((412601 : Nat) = 412601) ∧ False) ∨ ((((412602 : Nat) = 412602) ∧ False) ∨ ((((412603 : Nat) = 412603) ∧ False) ∨ ((((412604 : Nat) = 412604) ∧ False) ∨ ((((412605 : Nat) = 412605) ∧ False) ∨ (((412606 : Nat) = 412606) ∧ False)))))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

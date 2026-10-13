import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v121 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (a = 0) := by
  sorry

end FateCurriculum

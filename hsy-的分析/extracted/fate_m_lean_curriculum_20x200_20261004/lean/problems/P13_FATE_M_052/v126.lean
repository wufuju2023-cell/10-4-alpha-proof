import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 126/200; tier 4: disjunction_elimination. -/
theorem fate_m_052_v126 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_cases : ((b * c * a = 1) ∨ ((((412601 : Nat) = 412601) ∧ False) ∨ ((((412602 : Nat) = 412602) ∧ False) ∨ ((((412603 : Nat) = 412603) ∧ False) ∨ ((((412604 : Nat) = 412604) ∧ False) ∨ ((((412605 : Nat) = 412605) ∧ False) ∨ (((412606 : Nat) = 412606) ∧ False)))))))) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

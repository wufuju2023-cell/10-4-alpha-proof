import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_052_v122 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_cases : ((b * c * a = 1) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

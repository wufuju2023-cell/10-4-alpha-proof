import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_052_v124 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_cases : ((b * c * a = 1) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

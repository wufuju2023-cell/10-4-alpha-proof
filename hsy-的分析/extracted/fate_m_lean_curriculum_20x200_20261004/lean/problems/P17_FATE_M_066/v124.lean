import Mathlib

namespace FateCurriculum

/-- FATE-M #66, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_066_v124 {G : Type*} [Group G] (a b c : G)
    (curriculum_cases : ((∃! x : G, a * x * b = c) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (∃! x : G, a * x * b = c) := by
  sorry

end FateCurriculum

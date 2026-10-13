import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_061_v124 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_cases : (((M ⊓ N).Normal) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

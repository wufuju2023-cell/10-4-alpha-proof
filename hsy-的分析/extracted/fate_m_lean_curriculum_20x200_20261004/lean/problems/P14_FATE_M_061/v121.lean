import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_061_v121 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_cases : (((M ⊓ N).Normal) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

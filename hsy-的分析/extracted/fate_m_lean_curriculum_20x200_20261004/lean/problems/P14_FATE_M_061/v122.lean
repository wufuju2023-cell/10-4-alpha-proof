import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_061_v122 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_cases : (((M ⊓ N).Normal) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

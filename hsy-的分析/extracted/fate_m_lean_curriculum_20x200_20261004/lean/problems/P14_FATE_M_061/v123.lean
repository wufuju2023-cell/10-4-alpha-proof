import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_061_v123 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_cases : (((M ⊓ N).Normal) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

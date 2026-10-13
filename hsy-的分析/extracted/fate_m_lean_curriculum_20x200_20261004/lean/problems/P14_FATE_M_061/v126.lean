import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 126/200; tier 4: disjunction_elimination. -/
theorem fate_m_061_v126 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_cases : (((M ⊓ N).Normal) ∨ ((((412601 : Nat) = 412601) ∧ False) ∨ ((((412602 : Nat) = 412602) ∧ False) ∨ ((((412603 : Nat) = 412603) ∧ False) ∨ ((((412604 : Nat) = 412604) ∧ False) ∨ ((((412605 : Nat) = 412605) ∧ False) ∨ (((412606 : Nat) = 412606) ∧ False)))))))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

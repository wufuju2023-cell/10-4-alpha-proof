import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 177/200; tier 6: unscaffolded. -/
theorem fate_m_061_v177 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal] :
    (((M ⊓ N).Normal) ∧ ((617701 : Nat) = 617701)) := by
  sorry

end FateCurriculum

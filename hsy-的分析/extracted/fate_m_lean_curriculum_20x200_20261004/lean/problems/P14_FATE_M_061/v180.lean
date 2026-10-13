import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 180/200; tier 6: unscaffolded. -/
theorem fate_m_061_v180 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal] :
    (((M ⊓ N).Normal) ∧ (((618001 : Nat) = 618001) ∧ (((618002 : Nat) = 618002) ∧ (((618003 : Nat) = 618003) ∧ ((618004 : Nat) = 618004))))) := by
  sorry

end FateCurriculum

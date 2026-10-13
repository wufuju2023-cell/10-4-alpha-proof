import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 178/200; tier 6: unscaffolded. -/
theorem fate_m_061_v178 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal] :
    (((M ⊓ N).Normal) ∧ (((617801 : Nat) = 617801) ∧ ((617802 : Nat) = 617802))) := by
  sorry

end FateCurriculum

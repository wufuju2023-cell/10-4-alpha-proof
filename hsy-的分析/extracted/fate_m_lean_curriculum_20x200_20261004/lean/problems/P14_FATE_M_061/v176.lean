import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 176/200; tier 6: unscaffolded. -/
theorem fate_m_061_v176 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal] :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

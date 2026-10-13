import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_061_v004 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_target : ((M ⊓ N).Normal))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_009_v004 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

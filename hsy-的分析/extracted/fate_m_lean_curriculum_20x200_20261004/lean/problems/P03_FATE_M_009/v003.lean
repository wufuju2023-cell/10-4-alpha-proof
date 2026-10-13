import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 3/200; tier 1: direct_target. -/
theorem fate_m_009_v003 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((100301 : Nat) = 100301))
    (curriculum_context_002 : ((100302 : Nat) = 100302)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

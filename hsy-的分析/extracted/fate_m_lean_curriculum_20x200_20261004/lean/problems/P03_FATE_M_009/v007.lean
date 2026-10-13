import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 7/200; tier 1: direct_target. -/
theorem fate_m_009_v007 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((100701 : Nat) = 100701))
    (curriculum_context_002 : ((100702 : Nat) = 100702))
    (curriculum_context_003 : ((100703 : Nat) = 100703))
    (curriculum_context_004 : ((100704 : Nat) = 100704))
    (curriculum_context_005 : ((100705 : Nat) = 100705))
    (curriculum_context_006 : ((100706 : Nat) = 100706)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

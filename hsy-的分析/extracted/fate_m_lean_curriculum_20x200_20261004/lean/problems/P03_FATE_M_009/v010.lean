import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 10/200; tier 1: direct_target. -/
theorem fate_m_009_v010 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((101001 : Nat) = 101001))
    (curriculum_context_002 : ((101002 : Nat) = 101002))
    (curriculum_context_003 : ((101003 : Nat) = 101003))
    (curriculum_context_004 : ((101004 : Nat) = 101004))
    (curriculum_context_005 : ((101005 : Nat) = 101005))
    (curriculum_context_006 : ((101006 : Nat) = 101006))
    (curriculum_context_007 : ((101007 : Nat) = 101007))
    (curriculum_context_008 : ((101008 : Nat) = 101008))
    (curriculum_context_009 : ((101009 : Nat) = 101009)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

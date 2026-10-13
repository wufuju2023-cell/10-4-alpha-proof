import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 11/200; tier 1: direct_target. -/
theorem fate_m_009_v011 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((101101 : Nat) = 101101))
    (curriculum_context_002 : ((101102 : Nat) = 101102))
    (curriculum_context_003 : ((101103 : Nat) = 101103))
    (curriculum_context_004 : ((101104 : Nat) = 101104))
    (curriculum_context_005 : ((101105 : Nat) = 101105))
    (curriculum_context_006 : ((101106 : Nat) = 101106))
    (curriculum_context_007 : ((101107 : Nat) = 101107))
    (curriculum_context_008 : ((101108 : Nat) = 101108))
    (curriculum_context_009 : ((101109 : Nat) = 101109))
    (curriculum_context_010 : ((101110 : Nat) = 101110)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

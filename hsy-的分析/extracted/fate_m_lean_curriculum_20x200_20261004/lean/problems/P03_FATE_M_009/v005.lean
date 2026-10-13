import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_009_v005 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_target : (orderOf a = 0))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

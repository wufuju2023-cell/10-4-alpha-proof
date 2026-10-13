import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 161/200; tier 5: iff_bridge. -/
theorem fate_m_009_v161 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_bridge_001 : ((orderOf a = 0) ↔ ((516101 : Nat) = 516101)))
    (curriculum_fact : ((516101 : Nat) = 516101)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

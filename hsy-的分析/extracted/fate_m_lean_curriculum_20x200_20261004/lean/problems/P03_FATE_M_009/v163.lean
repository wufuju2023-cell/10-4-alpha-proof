import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 163/200; tier 5: iff_bridge. -/
theorem fate_m_009_v163 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_bridge_001 : ((orderOf a = 0) ↔ ((516301 : Nat) = 516301)))
    (curriculum_bridge_002 : (((516301 : Nat) = 516301) ↔ ((516302 : Nat) = 516302)))
    (curriculum_bridge_003 : (((516302 : Nat) = 516302) ↔ ((516303 : Nat) = 516303)))
    (curriculum_fact : ((516303 : Nat) = 516303)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

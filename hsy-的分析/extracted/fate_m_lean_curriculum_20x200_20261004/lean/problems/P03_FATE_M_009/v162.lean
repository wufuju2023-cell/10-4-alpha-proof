import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 162/200; tier 5: iff_bridge. -/
theorem fate_m_009_v162 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_bridge_001 : ((orderOf a = 0) ↔ ((516201 : Nat) = 516201)))
    (curriculum_bridge_002 : (((516201 : Nat) = 516201) ↔ ((516202 : Nat) = 516202)))
    (curriculum_fact : ((516202 : Nat) = 516202)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

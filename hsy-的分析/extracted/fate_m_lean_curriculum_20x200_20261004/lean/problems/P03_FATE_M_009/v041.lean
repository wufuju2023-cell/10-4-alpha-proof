import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 41/200; tier 2: packed_target. -/
theorem fate_m_009_v041 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204101 : Nat) = 204101) ∧ (orderOf a = 0))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

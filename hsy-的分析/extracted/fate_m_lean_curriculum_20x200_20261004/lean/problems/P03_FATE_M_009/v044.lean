import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 44/200; tier 2: packed_target. -/
theorem fate_m_009_v044 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204401 : Nat) = 204401) ∧ (((204402 : Nat) = 204402) ∧ (((204403 : Nat) = 204403) ∧ (((204404 : Nat) = 204404) ∧ (orderOf a = 0)))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

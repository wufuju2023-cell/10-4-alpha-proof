import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 45/200; tier 2: packed_target. -/
theorem fate_m_009_v045 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204501 : Nat) = 204501) ∧ (((204502 : Nat) = 204502) ∧ (((204503 : Nat) = 204503) ∧ (((204504 : Nat) = 204504) ∧ (((204505 : Nat) = 204505) ∧ (orderOf a = 0))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 46/200; tier 2: packed_target. -/
theorem fate_m_009_v046 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204601 : Nat) = 204601) ∧ (((204602 : Nat) = 204602) ∧ (((204603 : Nat) = 204603) ∧ (((204604 : Nat) = 204604) ∧ (((204605 : Nat) = 204605) ∧ (((204606 : Nat) = 204606) ∧ (orderOf a = 0)))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

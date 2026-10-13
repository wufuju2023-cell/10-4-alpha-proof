import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 42/200; tier 2: packed_target. -/
theorem fate_m_009_v042 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204201 : Nat) = 204201) ∧ (((204202 : Nat) = 204202) ∧ (orderOf a = 0)))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

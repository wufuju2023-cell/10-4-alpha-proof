import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 177/200; tier 6: unscaffolded. -/
theorem fate_m_009_v177 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0) :
    ((orderOf a = 0) ∧ ((617701 : Nat) = 617701)) := by
  sorry

end FateCurriculum

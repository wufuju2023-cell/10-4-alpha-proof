import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 178/200; tier 6: unscaffolded. -/
theorem fate_m_009_v178 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0) :
    ((orderOf a = 0) ∧ (((617801 : Nat) = 617801) ∧ ((617802 : Nat) = 617802))) := by
  sorry

end FateCurriculum

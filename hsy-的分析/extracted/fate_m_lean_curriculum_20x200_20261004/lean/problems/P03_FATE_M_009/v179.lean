import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 179/200; tier 6: unscaffolded. -/
theorem fate_m_009_v179 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0) :
    ((orderOf a = 0) ∧ (((617901 : Nat) = 617901) ∧ (((617902 : Nat) = 617902) ∧ ((617903 : Nat) = 617903)))) := by
  sorry

end FateCurriculum

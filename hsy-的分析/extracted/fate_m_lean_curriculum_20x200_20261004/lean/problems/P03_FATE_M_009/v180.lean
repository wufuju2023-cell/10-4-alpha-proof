import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 180/200; tier 6: unscaffolded. -/
theorem fate_m_009_v180 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0) :
    ((orderOf a = 0) ∧ (((618001 : Nat) = 618001) ∧ (((618002 : Nat) = 618002) ∧ (((618003 : Nat) = 618003) ∧ ((618004 : Nat) = 618004))))) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v121 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

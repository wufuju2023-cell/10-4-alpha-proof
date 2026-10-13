import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v122 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

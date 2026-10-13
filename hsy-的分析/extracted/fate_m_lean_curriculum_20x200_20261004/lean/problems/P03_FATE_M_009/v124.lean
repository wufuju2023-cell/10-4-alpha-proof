import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v124 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

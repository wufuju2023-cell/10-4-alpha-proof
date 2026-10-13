import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 125/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v125 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ ((((412501 : Nat) = 412501) ∧ False) ∨ ((((412502 : Nat) = 412502) ∧ False) ∨ ((((412503 : Nat) = 412503) ∧ False) ∨ ((((412504 : Nat) = 412504) ∧ False) ∨ (((412505 : Nat) = 412505) ∧ False))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

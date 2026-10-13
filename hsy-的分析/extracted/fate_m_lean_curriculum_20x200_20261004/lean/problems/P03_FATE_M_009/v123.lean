import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v123 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

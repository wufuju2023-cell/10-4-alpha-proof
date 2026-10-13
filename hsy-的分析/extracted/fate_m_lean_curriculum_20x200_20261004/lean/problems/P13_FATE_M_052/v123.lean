import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_052_v123 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_cases : ((b * c * a = 1) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

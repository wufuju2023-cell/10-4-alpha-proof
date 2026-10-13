import Mathlib

namespace FateCurriculum

/-- FATE-M #66, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_066_v123 {G : Type*} [Group G] (a b c : G)
    (curriculum_cases : ((∃! x : G, a * x * b = c) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (∃! x : G, a * x * b = c) := by
  sorry

end FateCurriculum

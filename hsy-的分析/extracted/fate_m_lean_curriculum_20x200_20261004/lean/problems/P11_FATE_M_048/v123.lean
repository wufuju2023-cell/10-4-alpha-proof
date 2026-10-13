import Mathlib

namespace FateCurriculum

/-- FATE-M #48, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_048_v123 {G : Type*} [Group G] (a b : G) (h : a * b = b * a⁻¹)
    (curriculum_cases : ((b * a = a⁻¹ * b) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (b * a = a⁻¹ * b) := by
  sorry

end FateCurriculum

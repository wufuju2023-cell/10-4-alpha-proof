import Mathlib

namespace FateCurriculum

/-- FATE-M #51, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_051_v121 {G : Type*} [Group G] (a b : G) (h : (a * b) ^ 2 = a ^ 2 * b ^ 2)
    (curriculum_cases : ((a * b = b * a) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (a * b = b * a) := by
  sorry

end FateCurriculum

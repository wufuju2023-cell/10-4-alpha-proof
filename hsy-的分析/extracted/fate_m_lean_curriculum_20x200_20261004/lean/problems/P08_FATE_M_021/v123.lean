import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_021_v123 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_cases : ((∃! x, a * x + b = 0) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

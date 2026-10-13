import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_021_v121 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_cases : ((∃! x, a * x + b = 0) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

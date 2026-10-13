import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_021_v124 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_cases : ((∃! x, a * x + b = 0) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

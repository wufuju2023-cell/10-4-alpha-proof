import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_011_v124 {R : Type*} [Ring R] (a b c : R)
    (curriculum_cases : ((a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

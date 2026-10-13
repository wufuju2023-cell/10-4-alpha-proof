import Mathlib

open ComplexConjugate

namespace FateCurriculum

/-- FATE-M #35, curriculum variant 122/200; tier 4: disjunction_elimination. -/
theorem fate_m_035_v122 (curriculum_cases : (({z : ℂ | conj z = z} = {z : ℂ | ∃ (x : ℝ), z = x}) ∨ ((((412201 : Nat) = 412201) ∧ False) ∨ (((412202 : Nat) = 412202) ∧ False)))) :
    ({z : ℂ | conj z = z} = {z : ℂ | ∃ (x : ℝ), z = x}) := by
  sorry

end FateCurriculum

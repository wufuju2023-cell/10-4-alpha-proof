import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 162/200; tier 5: iff_bridge. -/
theorem fate_m_014_v162 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_bridge_001 : ((a = 0) ↔ ((516201 : Nat) = 516201)))
    (curriculum_bridge_002 : (((516201 : Nat) = 516201) ↔ ((516202 : Nat) = 516202)))
    (curriculum_fact : ((516202 : Nat) = 516202)) :
    (a = 0) := by
  sorry

end FateCurriculum

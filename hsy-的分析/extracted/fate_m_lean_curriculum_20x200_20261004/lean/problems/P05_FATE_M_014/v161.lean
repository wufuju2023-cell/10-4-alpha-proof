import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 161/200; tier 5: iff_bridge. -/
theorem fate_m_014_v161 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_bridge_001 : ((a = 0) ↔ ((516101 : Nat) = 516101)))
    (curriculum_fact : ((516101 : Nat) = 516101)) :
    (a = 0) := by
  sorry

end FateCurriculum

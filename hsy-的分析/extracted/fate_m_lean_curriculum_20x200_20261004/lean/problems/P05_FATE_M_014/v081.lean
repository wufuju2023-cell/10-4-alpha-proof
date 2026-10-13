import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 81/200; tier 3: implication_chain. -/
theorem fate_m_014_v081 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_step : (((308101 : Nat) = 308101) → (a = 0)))
    (curriculum_fact_001 : ((308101 : Nat) = 308101)) :
    (a = 0) := by
  sorry

end FateCurriculum

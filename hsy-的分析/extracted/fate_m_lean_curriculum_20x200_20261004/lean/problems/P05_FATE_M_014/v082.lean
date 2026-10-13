import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 82/200; tier 3: implication_chain. -/
theorem fate_m_014_v082 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_step : (((308201 : Nat) = 308201) → (((308202 : Nat) = 308202) → (a = 0))))
    (curriculum_fact_001 : ((308201 : Nat) = 308201))
    (curriculum_fact_002 : ((308202 : Nat) = 308202)) :
    (a = 0) := by
  sorry

end FateCurriculum

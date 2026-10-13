import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_014_v004 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_target : (a = 0))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (a = 0) := by
  sorry

end FateCurriculum

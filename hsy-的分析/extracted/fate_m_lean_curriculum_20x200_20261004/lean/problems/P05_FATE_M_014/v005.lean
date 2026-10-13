import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_014_v005 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_target : (a = 0))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    (a = 0) := by
  sorry

end FateCurriculum

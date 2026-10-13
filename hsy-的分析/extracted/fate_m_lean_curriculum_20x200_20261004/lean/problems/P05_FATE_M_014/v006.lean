import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 6/200; tier 1: direct_target. -/
theorem fate_m_014_v006 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_target : (a = 0))
    (curriculum_context_001 : ((100601 : Nat) = 100601))
    (curriculum_context_002 : ((100602 : Nat) = 100602))
    (curriculum_context_003 : ((100603 : Nat) = 100603))
    (curriculum_context_004 : ((100604 : Nat) = 100604))
    (curriculum_context_005 : ((100605 : Nat) = 100605)) :
    (a = 0) := by
  sorry

end FateCurriculum

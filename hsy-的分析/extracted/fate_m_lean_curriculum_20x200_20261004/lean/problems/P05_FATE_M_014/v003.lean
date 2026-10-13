import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 3/200; tier 1: direct_target. -/
theorem fate_m_014_v003 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_target : (a = 0))
    (curriculum_context_001 : ((100301 : Nat) = 100301))
    (curriculum_context_002 : ((100302 : Nat) = 100302)) :
    (a = 0) := by
  sorry

end FateCurriculum

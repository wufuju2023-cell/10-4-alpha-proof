import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 3/200; tier 1: direct_target. -/
theorem fate_m_015_v003 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_target : (y = z))
    (curriculum_context_001 : ((100301 : Nat) = 100301))
    (curriculum_context_002 : ((100302 : Nat) = 100302)) :
    (y = z) := by
  sorry

end FateCurriculum

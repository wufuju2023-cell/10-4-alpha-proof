import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_015_v004 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_target : (y = z))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (y = z) := by
  sorry

end FateCurriculum

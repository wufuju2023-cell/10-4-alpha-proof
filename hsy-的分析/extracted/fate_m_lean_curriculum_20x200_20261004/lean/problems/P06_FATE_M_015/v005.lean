import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_015_v005 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_target : (y = z))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    (y = z) := by
  sorry

end FateCurriculum

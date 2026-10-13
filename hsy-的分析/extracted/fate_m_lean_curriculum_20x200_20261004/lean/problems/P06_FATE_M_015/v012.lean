import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 12/200; tier 1: direct_target. -/
theorem fate_m_015_v012 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_target : (y = z))
    (curriculum_context_001 : ((101201 : Nat) = 101201))
    (curriculum_context_002 : ((101202 : Nat) = 101202))
    (curriculum_context_003 : ((101203 : Nat) = 101203))
    (curriculum_context_004 : ((101204 : Nat) = 101204))
    (curriculum_context_005 : ((101205 : Nat) = 101205))
    (curriculum_context_006 : ((101206 : Nat) = 101206))
    (curriculum_context_007 : ((101207 : Nat) = 101207))
    (curriculum_context_008 : ((101208 : Nat) = 101208))
    (curriculum_context_009 : ((101209 : Nat) = 101209))
    (curriculum_context_010 : ((101210 : Nat) = 101210))
    (curriculum_context_011 : ((101211 : Nat) = 101211)) :
    (y = z) := by
  sorry

end FateCurriculum

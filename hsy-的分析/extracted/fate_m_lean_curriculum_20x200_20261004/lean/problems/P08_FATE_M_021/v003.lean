import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 3/200; tier 1: direct_target. -/
theorem fate_m_021_v003 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_target : (∃! x, a * x + b = 0))
    (curriculum_context_001 : ((100301 : Nat) = 100301))
    (curriculum_context_002 : ((100302 : Nat) = 100302)) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

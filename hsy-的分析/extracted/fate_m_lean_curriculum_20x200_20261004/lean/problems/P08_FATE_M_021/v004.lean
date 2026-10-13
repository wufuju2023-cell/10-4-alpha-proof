import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_021_v004 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_target : (∃! x, a * x + b = 0))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #21, curriculum variant 2/200; tier 1: direct_target. -/
theorem fate_m_021_v002 {F : Type*} [Field F] {a : Fˣ} {b : F}
    (curriculum_target : (∃! x, a * x + b = 0))
    (curriculum_context_001 : ((100201 : Nat) = 100201)) :
    (∃! x, a * x + b = 0) := by
  sorry

end FateCurriculum

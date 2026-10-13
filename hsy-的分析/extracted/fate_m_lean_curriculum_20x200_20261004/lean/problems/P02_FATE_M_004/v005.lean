import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_004_v005 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_target : ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

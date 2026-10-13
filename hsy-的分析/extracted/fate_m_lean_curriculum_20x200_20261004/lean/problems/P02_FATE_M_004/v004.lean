import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_004_v004 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_target : ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

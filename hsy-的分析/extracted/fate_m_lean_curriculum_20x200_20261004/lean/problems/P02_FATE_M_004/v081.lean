import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 81/200; tier 3: implication_chain. -/
theorem fate_m_004_v081 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_step : (((308101 : Nat) = 308101) → ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker))))
    (curriculum_fact_001 : ((308101 : Nat) = 308101)) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

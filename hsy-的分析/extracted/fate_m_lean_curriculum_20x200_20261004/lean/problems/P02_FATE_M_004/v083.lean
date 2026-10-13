import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 83/200; tier 3: implication_chain. -/
theorem fate_m_004_v083 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_step : (((308301 : Nat) = 308301) → (((308302 : Nat) = 308302) → (((308303 : Nat) = 308303) → ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker))))))
    (curriculum_fact_001 : ((308301 : Nat) = 308301))
    (curriculum_fact_002 : ((308302 : Nat) = 308302))
    (curriculum_fact_003 : ((308303 : Nat) = 308303)) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

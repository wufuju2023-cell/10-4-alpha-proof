import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v121 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v123 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 126/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v126 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((412601 : Nat) = 412601) ∧ False) ∨ ((((412602 : Nat) = 412602) ∧ False) ∨ ((((412603 : Nat) = 412603) ∧ False) ∨ ((((412604 : Nat) = 412604) ∧ False) ∨ ((((412605 : Nat) = 412605) ∧ False) ∨ (((412606 : Nat) = 412606) ∧ False)))))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

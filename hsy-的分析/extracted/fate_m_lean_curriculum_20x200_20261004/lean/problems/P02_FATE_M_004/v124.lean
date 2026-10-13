import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v124 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

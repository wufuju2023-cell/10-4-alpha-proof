import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 125/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v125 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((412501 : Nat) = 412501) ∧ False) ∨ ((((412502 : Nat) = 412502) ∧ False) ∨ ((((412503 : Nat) = 412503) ∧ False) ∨ ((((412504 : Nat) = 412504) ∧ False) ∨ (((412505 : Nat) = 412505) ∧ False))))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 134/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v134 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((413401 : Nat) = 413401) ∧ False) ∨ ((((413402 : Nat) = 413402) ∧ False) ∨ ((((413403 : Nat) = 413403) ∧ False) ∨ ((((413404 : Nat) = 413404) ∧ False) ∨ ((((413405 : Nat) = 413405) ∧ False) ∨ ((((413406 : Nat) = 413406) ∧ False) ∨ ((((413407 : Nat) = 413407) ∧ False) ∨ ((((413408 : Nat) = 413408) ∧ False) ∨ ((((413409 : Nat) = 413409) ∧ False) ∨ ((((413410 : Nat) = 413410) ∧ False) ∨ ((((413411 : Nat) = 413411) ∧ False) ∨ ((((413412 : Nat) = 413412) ∧ False) ∨ ((((413413 : Nat) = 413413) ∧ False) ∨ (((413414 : Nat) = 413414) ∧ False)))))))))))))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

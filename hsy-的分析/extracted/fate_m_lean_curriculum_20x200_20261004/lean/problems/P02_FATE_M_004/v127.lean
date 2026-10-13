import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 127/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v127 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((412701 : Nat) = 412701) ∧ False) ∨ ((((412702 : Nat) = 412702) ∧ False) ∨ ((((412703 : Nat) = 412703) ∧ False) ∨ ((((412704 : Nat) = 412704) ∧ False) ∨ ((((412705 : Nat) = 412705) ∧ False) ∨ ((((412706 : Nat) = 412706) ∧ False) ∨ (((412707 : Nat) = 412707) ∧ False))))))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

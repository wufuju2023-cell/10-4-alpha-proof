import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 130/200; tier 4: disjunction_elimination. -/
theorem fate_m_004_v130 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_cases : (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∨ ((((413001 : Nat) = 413001) ∧ False) ∨ ((((413002 : Nat) = 413002) ∧ False) ∨ ((((413003 : Nat) = 413003) ∧ False) ∨ ((((413004 : Nat) = 413004) ∧ False) ∨ ((((413005 : Nat) = 413005) ∧ False) ∨ ((((413006 : Nat) = 413006) ∧ False) ∨ ((((413007 : Nat) = 413007) ∧ False) ∨ ((((413008 : Nat) = 413008) ∧ False) ∨ ((((413009 : Nat) = 413009) ∧ False) ∨ (((413010 : Nat) = 413010) ∧ False)))))))))))) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

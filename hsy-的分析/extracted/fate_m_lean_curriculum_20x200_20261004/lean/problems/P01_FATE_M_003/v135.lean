import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 135/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v135 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((413501 : Nat) = 413501) ∧ False) ∨ ((((413502 : Nat) = 413502) ∧ False) ∨ ((((413503 : Nat) = 413503) ∧ False) ∨ ((((413504 : Nat) = 413504) ∧ False) ∨ ((((413505 : Nat) = 413505) ∧ False) ∨ ((((413506 : Nat) = 413506) ∧ False) ∨ ((((413507 : Nat) = 413507) ∧ False) ∨ ((((413508 : Nat) = 413508) ∧ False) ∨ ((((413509 : Nat) = 413509) ∧ False) ∨ ((((413510 : Nat) = 413510) ∧ False) ∨ ((((413511 : Nat) = 413511) ∧ False) ∨ ((((413512 : Nat) = 413512) ∧ False) ∨ ((((413513 : Nat) = 413513) ∧ False) ∨ ((((413514 : Nat) = 413514) ∧ False) ∨ (((413515 : Nat) = 413515) ∧ False))))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

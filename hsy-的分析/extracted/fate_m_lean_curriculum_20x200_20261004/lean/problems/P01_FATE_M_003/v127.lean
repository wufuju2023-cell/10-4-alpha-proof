import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 127/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v127 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((412701 : Nat) = 412701) ∧ False) ∨ ((((412702 : Nat) = 412702) ∧ False) ∨ ((((412703 : Nat) = 412703) ∧ False) ∨ ((((412704 : Nat) = 412704) ∧ False) ∨ ((((412705 : Nat) = 412705) ∧ False) ∨ ((((412706 : Nat) = 412706) ∧ False) ∨ (((412707 : Nat) = 412707) ∧ False))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

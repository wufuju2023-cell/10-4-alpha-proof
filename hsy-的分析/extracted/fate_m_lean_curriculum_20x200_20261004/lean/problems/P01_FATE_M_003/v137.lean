import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 137/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v137 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((413701 : Nat) = 413701) ∧ False) ∨ ((((413702 : Nat) = 413702) ∧ False) ∨ ((((413703 : Nat) = 413703) ∧ False) ∨ ((((413704 : Nat) = 413704) ∧ False) ∨ ((((413705 : Nat) = 413705) ∧ False) ∨ ((((413706 : Nat) = 413706) ∧ False) ∨ ((((413707 : Nat) = 413707) ∧ False) ∨ ((((413708 : Nat) = 413708) ∧ False) ∨ ((((413709 : Nat) = 413709) ∧ False) ∨ ((((413710 : Nat) = 413710) ∧ False) ∨ ((((413711 : Nat) = 413711) ∧ False) ∨ ((((413712 : Nat) = 413712) ∧ False) ∨ ((((413713 : Nat) = 413713) ∧ False) ∨ ((((413714 : Nat) = 413714) ∧ False) ∨ ((((413715 : Nat) = 413715) ∧ False) ∨ ((((413716 : Nat) = 413716) ∧ False) ∨ (((413717 : Nat) = 413717) ∧ False))))))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

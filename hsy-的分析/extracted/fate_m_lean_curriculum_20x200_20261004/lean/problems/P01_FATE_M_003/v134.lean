import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 134/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v134 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((413401 : Nat) = 413401) ∧ False) ∨ ((((413402 : Nat) = 413402) ∧ False) ∨ ((((413403 : Nat) = 413403) ∧ False) ∨ ((((413404 : Nat) = 413404) ∧ False) ∨ ((((413405 : Nat) = 413405) ∧ False) ∨ ((((413406 : Nat) = 413406) ∧ False) ∨ ((((413407 : Nat) = 413407) ∧ False) ∨ ((((413408 : Nat) = 413408) ∧ False) ∨ ((((413409 : Nat) = 413409) ∧ False) ∨ ((((413410 : Nat) = 413410) ∧ False) ∨ ((((413411 : Nat) = 413411) ∧ False) ∨ ((((413412 : Nat) = 413412) ∧ False) ∨ ((((413413 : Nat) = 413413) ∧ False) ∨ (((413414 : Nat) = 413414) ∧ False)))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

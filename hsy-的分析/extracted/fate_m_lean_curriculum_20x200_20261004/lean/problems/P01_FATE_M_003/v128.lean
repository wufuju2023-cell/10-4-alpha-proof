import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 128/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v128 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((412801 : Nat) = 412801) ∧ False) ∨ ((((412802 : Nat) = 412802) ∧ False) ∨ ((((412803 : Nat) = 412803) ∧ False) ∨ ((((412804 : Nat) = 412804) ∧ False) ∨ ((((412805 : Nat) = 412805) ∧ False) ∨ ((((412806 : Nat) = 412806) ∧ False) ∨ ((((412807 : Nat) = 412807) ∧ False) ∨ (((412808 : Nat) = 412808) ∧ False)))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

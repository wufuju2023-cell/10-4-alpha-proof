import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 124/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v124 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((412401 : Nat) = 412401) ∧ False) ∨ ((((412402 : Nat) = 412402) ∧ False) ∨ ((((412403 : Nat) = 412403) ∧ False) ∨ (((412404 : Nat) = 412404) ∧ False)))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

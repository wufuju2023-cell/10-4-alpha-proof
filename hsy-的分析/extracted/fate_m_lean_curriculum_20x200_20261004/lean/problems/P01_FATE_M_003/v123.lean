import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 123/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v123 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((412301 : Nat) = 412301) ∧ False) ∨ ((((412302 : Nat) = 412302) ∧ False) ∨ (((412303 : Nat) = 412303) ∧ False))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

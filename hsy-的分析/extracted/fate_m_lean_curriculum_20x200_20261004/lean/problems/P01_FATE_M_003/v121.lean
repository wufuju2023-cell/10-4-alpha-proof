import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 121/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v121 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ (((412101 : Nat) = 412101) ∧ False))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

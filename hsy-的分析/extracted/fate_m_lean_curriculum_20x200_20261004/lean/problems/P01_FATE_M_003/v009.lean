import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 9/200; tier 1: direct_target. -/
theorem fate_m_003_v009 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_target : (Function.Surjective (g.comp f)))
    (curriculum_context_001 : ((100901 : Nat) = 100901))
    (curriculum_context_002 : ((100902 : Nat) = 100902))
    (curriculum_context_003 : ((100903 : Nat) = 100903))
    (curriculum_context_004 : ((100904 : Nat) = 100904))
    (curriculum_context_005 : ((100905 : Nat) = 100905))
    (curriculum_context_006 : ((100906 : Nat) = 100906))
    (curriculum_context_007 : ((100907 : Nat) = 100907))
    (curriculum_context_008 : ((100908 : Nat) = 100908)) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 176/200; tier 6: unscaffolded. -/
theorem fate_m_003_v176 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

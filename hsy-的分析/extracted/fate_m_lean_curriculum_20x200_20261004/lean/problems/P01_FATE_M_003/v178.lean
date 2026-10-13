import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 178/200; tier 6: unscaffolded. -/
theorem fate_m_003_v178 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g) :
    ((Function.Surjective (g.comp f)) ∧ (((617801 : Nat) = 617801) ∧ ((617802 : Nat) = 617802))) := by
  sorry

end FateCurriculum

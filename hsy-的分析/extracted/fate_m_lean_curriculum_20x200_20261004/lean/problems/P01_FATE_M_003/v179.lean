import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 179/200; tier 6: unscaffolded. -/
theorem fate_m_003_v179 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g) :
    ((Function.Surjective (g.comp f)) ∧ (((617901 : Nat) = 617901) ∧ (((617902 : Nat) = 617902) ∧ ((617903 : Nat) = 617903)))) := by
  sorry

end FateCurriculum

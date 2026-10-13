import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 183/200; tier 6: unscaffolded. -/
theorem fate_m_003_v183 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g) :
    ((Function.Surjective (g.comp f)) ∧ (((618301 : Nat) = 618301) ∧ (((618302 : Nat) = 618302) ∧ (((618303 : Nat) = 618303) ∧ (((618304 : Nat) = 618304) ∧ (((618305 : Nat) = 618305) ∧ (((618306 : Nat) = 618306) ∧ ((618307 : Nat) = 618307)))))))) := by
  sorry

end FateCurriculum

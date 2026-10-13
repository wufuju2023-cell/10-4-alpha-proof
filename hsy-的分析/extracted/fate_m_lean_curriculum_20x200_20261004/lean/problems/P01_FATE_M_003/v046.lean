import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 46/200; tier 2: packed_target. -/
theorem fate_m_003_v046 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_pack : (((204601 : Nat) = 204601) ∧ (((204602 : Nat) = 204602) ∧ (((204603 : Nat) = 204603) ∧ (((204604 : Nat) = 204604) ∧ (((204605 : Nat) = 204605) ∧ (((204606 : Nat) = 204606) ∧ (Function.Surjective (g.comp f))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

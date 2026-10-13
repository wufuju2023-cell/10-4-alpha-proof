import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 44/200; tier 2: packed_target. -/
theorem fate_m_003_v044 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_pack : (((204401 : Nat) = 204401) ∧ (((204402 : Nat) = 204402) ∧ (((204403 : Nat) = 204403) ∧ (((204404 : Nat) = 204404) ∧ (Function.Surjective (g.comp f))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

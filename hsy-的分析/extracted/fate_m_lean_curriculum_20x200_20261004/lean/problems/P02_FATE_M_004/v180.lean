import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 180/200; tier 6: unscaffolded. -/
theorem fate_m_004_v180 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G} :
    (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∧ (((618001 : Nat) = 618001) ∧ (((618002 : Nat) = 618002) ∧ (((618003 : Nat) = 618003) ∧ ((618004 : Nat) = 618004))))) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 178/200; tier 6: unscaffolded. -/
theorem fate_m_004_v178 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G} :
    (((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) ∧ (((617801 : Nat) = 617801) ∧ ((617802 : Nat) = 617802))) := by
  sorry

end FateCurriculum

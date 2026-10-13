import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 176/200; tier 6: unscaffolded. -/
theorem fate_m_004_v176 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G} :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

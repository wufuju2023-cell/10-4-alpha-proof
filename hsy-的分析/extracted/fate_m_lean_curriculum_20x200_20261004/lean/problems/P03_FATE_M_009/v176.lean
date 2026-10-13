import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 176/200; tier 6: unscaffolded. -/
theorem fate_m_009_v176 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

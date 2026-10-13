import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 81/200; tier 3: implication_chain. -/
theorem fate_m_009_v081 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_step : (((308101 : Nat) = 308101) → (orderOf a = 0)))
    (curriculum_fact_001 : ((308101 : Nat) = 308101)) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

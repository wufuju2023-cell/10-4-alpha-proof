import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_052_v004 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_target : (b * c * a = 1))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 5/200; tier 1: direct_target. -/
theorem fate_m_052_v005 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_target : (b * c * a = 1))
    (curriculum_context_001 : ((100501 : Nat) = 100501))
    (curriculum_context_002 : ((100502 : Nat) = 100502))
    (curriculum_context_003 : ((100503 : Nat) = 100503))
    (curriculum_context_004 : ((100504 : Nat) = 100504)) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

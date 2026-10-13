import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 7/200; tier 1: direct_target. -/
theorem fate_m_011_v007 {R : Type*} [Ring R] (a b c : R)
    (curriculum_target : (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))
    (curriculum_context_001 : ((100701 : Nat) = 100701))
    (curriculum_context_002 : ((100702 : Nat) = 100702))
    (curriculum_context_003 : ((100703 : Nat) = 100703))
    (curriculum_context_004 : ((100704 : Nat) = 100704))
    (curriculum_context_005 : ((100705 : Nat) = 100705))
    (curriculum_context_006 : ((100706 : Nat) = 100706)) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

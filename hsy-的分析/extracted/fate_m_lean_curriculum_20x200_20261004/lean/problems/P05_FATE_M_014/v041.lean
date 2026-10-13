import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 41/200; tier 2: packed_target. -/
theorem fate_m_014_v041 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204101 : Nat) = 204101) ∧ (a = 0))) :
    (a = 0) := by
  sorry

end FateCurriculum

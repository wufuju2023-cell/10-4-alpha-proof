import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 42/200; tier 2: packed_target. -/
theorem fate_m_014_v042 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204201 : Nat) = 204201) ∧ (((204202 : Nat) = 204202) ∧ (a = 0)))) :
    (a = 0) := by
  sorry

end FateCurriculum

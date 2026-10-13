import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 43/200; tier 2: packed_target. -/
theorem fate_m_014_v043 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204301 : Nat) = 204301) ∧ (((204302 : Nat) = 204302) ∧ (((204303 : Nat) = 204303) ∧ (a = 0))))) :
    (a = 0) := by
  sorry

end FateCurriculum

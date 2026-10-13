import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 46/200; tier 2: packed_target. -/
theorem fate_m_014_v046 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204601 : Nat) = 204601) ∧ (((204602 : Nat) = 204602) ∧ (((204603 : Nat) = 204603) ∧ (((204604 : Nat) = 204604) ∧ (((204605 : Nat) = 204605) ∧ (((204606 : Nat) = 204606) ∧ (a = 0)))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

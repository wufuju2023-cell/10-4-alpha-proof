import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 45/200; tier 2: packed_target. -/
theorem fate_m_014_v045 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204501 : Nat) = 204501) ∧ (((204502 : Nat) = 204502) ∧ (((204503 : Nat) = 204503) ∧ (((204504 : Nat) = 204504) ∧ (((204505 : Nat) = 204505) ∧ (a = 0))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

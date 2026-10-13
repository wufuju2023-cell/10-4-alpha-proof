import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 45/200; tier 2: packed_target. -/
theorem fate_m_011_v045 {R : Type*} [Ring R] (a b c : R)
    (curriculum_pack : (((204501 : Nat) = 204501) ∧ (((204502 : Nat) = 204502) ∧ (((204503 : Nat) = 204503) ∧ (((204504 : Nat) = 204504) ∧ (((204505 : Nat) = 204505) ∧ (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

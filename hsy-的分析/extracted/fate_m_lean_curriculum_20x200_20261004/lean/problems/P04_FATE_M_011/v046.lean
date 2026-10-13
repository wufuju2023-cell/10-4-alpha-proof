import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 46/200; tier 2: packed_target. -/
theorem fate_m_011_v046 {R : Type*} [Ring R] (a b c : R)
    (curriculum_pack : (((204601 : Nat) = 204601) ∧ (((204602 : Nat) = 204602) ∧ (((204603 : Nat) = 204603) ∧ (((204604 : Nat) = 204604) ∧ (((204605 : Nat) = 204605) ∧ (((204606 : Nat) = 204606) ∧ (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a)))))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

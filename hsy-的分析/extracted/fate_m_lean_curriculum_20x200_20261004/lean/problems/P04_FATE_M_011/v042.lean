import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 42/200; tier 2: packed_target. -/
theorem fate_m_011_v042 {R : Type*} [Ring R] (a b c : R)
    (curriculum_pack : (((204201 : Nat) = 204201) ∧ (((204202 : Nat) = 204202) ∧ (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a)))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

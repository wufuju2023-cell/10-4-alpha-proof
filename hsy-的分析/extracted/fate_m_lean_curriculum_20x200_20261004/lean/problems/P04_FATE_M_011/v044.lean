import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 44/200; tier 2: packed_target. -/
theorem fate_m_011_v044 {R : Type*} [Ring R] (a b c : R)
    (curriculum_pack : (((204401 : Nat) = 204401) ∧ (((204402 : Nat) = 204402) ∧ (((204403 : Nat) = 204403) ∧ (((204404 : Nat) = 204404) ∧ (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a)))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

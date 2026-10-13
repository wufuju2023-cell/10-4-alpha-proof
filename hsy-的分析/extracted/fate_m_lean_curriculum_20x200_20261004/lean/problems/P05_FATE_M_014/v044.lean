import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 44/200; tier 2: packed_target. -/
theorem fate_m_014_v044 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204401 : Nat) = 204401) ∧ (((204402 : Nat) = 204402) ∧ (((204403 : Nat) = 204403) ∧ (((204404 : Nat) = 204404) ∧ (a = 0)))))) :
    (a = 0) := by
  sorry

end FateCurriculum

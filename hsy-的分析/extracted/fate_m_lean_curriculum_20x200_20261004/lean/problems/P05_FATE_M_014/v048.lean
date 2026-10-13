import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 48/200; tier 2: packed_target. -/
theorem fate_m_014_v048 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204801 : Nat) = 204801) ∧ (((204802 : Nat) = 204802) ∧ (((204803 : Nat) = 204803) ∧ (((204804 : Nat) = 204804) ∧ (((204805 : Nat) = 204805) ∧ (((204806 : Nat) = 204806) ∧ (((204807 : Nat) = 204807) ∧ (((204808 : Nat) = 204808) ∧ (a = 0)))))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

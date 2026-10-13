import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 47/200; tier 2: packed_target. -/
theorem fate_m_014_v047 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_pack : (((204701 : Nat) = 204701) ∧ (((204702 : Nat) = 204702) ∧ (((204703 : Nat) = 204703) ∧ (((204704 : Nat) = 204704) ∧ (((204705 : Nat) = 204705) ∧ (((204706 : Nat) = 204706) ∧ (((204707 : Nat) = 204707) ∧ (a = 0))))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

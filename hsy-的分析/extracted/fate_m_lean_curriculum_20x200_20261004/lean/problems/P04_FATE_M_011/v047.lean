import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 47/200; tier 2: packed_target. -/
theorem fate_m_011_v047 {R : Type*} [Ring R] (a b c : R)
    (curriculum_pack : (((204701 : Nat) = 204701) ∧ (((204702 : Nat) = 204702) ∧ (((204703 : Nat) = 204703) ∧ (((204704 : Nat) = 204704) ∧ (((204705 : Nat) = 204705) ∧ (((204706 : Nat) = 204706) ∧ (((204707 : Nat) = 204707) ∧ (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a))))))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

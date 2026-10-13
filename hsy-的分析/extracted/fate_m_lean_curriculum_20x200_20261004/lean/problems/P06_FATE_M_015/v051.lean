import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 51/200; tier 2: packed_target. -/
theorem fate_m_015_v051 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_pack : (((205101 : Nat) = 205101) ∧ (((205102 : Nat) = 205102) ∧ (((205103 : Nat) = 205103) ∧ (((205104 : Nat) = 205104) ∧ (((205105 : Nat) = 205105) ∧ (((205106 : Nat) = 205106) ∧ (((205107 : Nat) = 205107) ∧ (((205108 : Nat) = 205108) ∧ (((205109 : Nat) = 205109) ∧ (((205110 : Nat) = 205110) ∧ (((205111 : Nat) = 205111) ∧ (y = z))))))))))))) :
    (y = z) := by
  sorry

end FateCurriculum

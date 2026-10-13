import Mathlib

namespace FateCurriculum

/-- FATE-M #15, curriculum variant 52/200; tier 2: packed_target. -/
theorem fate_m_015_v052 {R : Type*} [Ring R] {x y z : R} (hx : x ≠ 0) (hy : x * y = 1)
    (hz : z * x = 1)
    (curriculum_pack : (((205201 : Nat) = 205201) ∧ (((205202 : Nat) = 205202) ∧ (((205203 : Nat) = 205203) ∧ (((205204 : Nat) = 205204) ∧ (((205205 : Nat) = 205205) ∧ (((205206 : Nat) = 205206) ∧ (((205207 : Nat) = 205207) ∧ (((205208 : Nat) = 205208) ∧ (((205209 : Nat) = 205209) ∧ (((205210 : Nat) = 205210) ∧ (((205211 : Nat) = 205211) ∧ (((205212 : Nat) = 205212) ∧ (y = z)))))))))))))) :
    (y = z) := by
  sorry

end FateCurriculum

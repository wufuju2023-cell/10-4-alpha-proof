import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 163/200; tier 5: iff_bridge. -/
theorem fate_m_014_v163 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_bridge_001 : ((a = 0) ↔ ((516301 : Nat) = 516301)))
    (curriculum_bridge_002 : (((516301 : Nat) = 516301) ↔ ((516302 : Nat) = 516302)))
    (curriculum_bridge_003 : (((516302 : Nat) = 516302) ↔ ((516303 : Nat) = 516303)))
    (curriculum_fact : ((516303 : Nat) = 516303)) :
    (a = 0) := by
  sorry

end FateCurriculum

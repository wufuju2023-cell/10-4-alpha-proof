import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 164/200; tier 5: iff_bridge. -/
theorem fate_m_014_v164 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_bridge_001 : ((a = 0) ↔ ((516401 : Nat) = 516401)))
    (curriculum_bridge_002 : (((516401 : Nat) = 516401) ↔ ((516402 : Nat) = 516402)))
    (curriculum_bridge_003 : (((516402 : Nat) = 516402) ↔ ((516403 : Nat) = 516403)))
    (curriculum_bridge_004 : (((516403 : Nat) = 516403) ↔ ((516404 : Nat) = 516404)))
    (curriculum_fact : ((516404 : Nat) = 516404)) :
    (a = 0) := by
  sorry

end FateCurriculum

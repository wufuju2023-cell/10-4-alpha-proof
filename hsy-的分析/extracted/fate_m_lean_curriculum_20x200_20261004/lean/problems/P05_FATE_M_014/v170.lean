import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 170/200; tier 5: iff_bridge. -/
theorem fate_m_014_v170 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_bridge_001 : ((a = 0) ↔ ((517001 : Nat) = 517001)))
    (curriculum_bridge_002 : (((517001 : Nat) = 517001) ↔ ((517002 : Nat) = 517002)))
    (curriculum_bridge_003 : (((517002 : Nat) = 517002) ↔ ((517003 : Nat) = 517003)))
    (curriculum_bridge_004 : (((517003 : Nat) = 517003) ↔ ((517004 : Nat) = 517004)))
    (curriculum_bridge_005 : (((517004 : Nat) = 517004) ↔ ((517005 : Nat) = 517005)))
    (curriculum_bridge_006 : (((517005 : Nat) = 517005) ↔ ((517006 : Nat) = 517006)))
    (curriculum_bridge_007 : (((517006 : Nat) = 517006) ↔ ((517007 : Nat) = 517007)))
    (curriculum_bridge_008 : (((517007 : Nat) = 517007) ↔ ((517008 : Nat) = 517008)))
    (curriculum_bridge_009 : (((517008 : Nat) = 517008) ↔ ((517009 : Nat) = 517009)))
    (curriculum_bridge_010 : (((517009 : Nat) = 517009) ↔ ((517010 : Nat) = 517010)))
    (curriculum_fact : ((517010 : Nat) = 517010)) :
    (a = 0) := by
  sorry

end FateCurriculum

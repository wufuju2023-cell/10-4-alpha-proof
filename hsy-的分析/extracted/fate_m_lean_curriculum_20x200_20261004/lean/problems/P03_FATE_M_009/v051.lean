import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 51/200; tier 2: packed_target. -/
theorem fate_m_009_v051 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((205101 : Nat) = 205101) ∧ (((205102 : Nat) = 205102) ∧ (((205103 : Nat) = 205103) ∧ (((205104 : Nat) = 205104) ∧ (((205105 : Nat) = 205105) ∧ (((205106 : Nat) = 205106) ∧ (((205107 : Nat) = 205107) ∧ (((205108 : Nat) = 205108) ∧ (((205109 : Nat) = 205109) ∧ (((205110 : Nat) = 205110) ∧ (((205111 : Nat) = 205111) ∧ (orderOf a = 0))))))))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

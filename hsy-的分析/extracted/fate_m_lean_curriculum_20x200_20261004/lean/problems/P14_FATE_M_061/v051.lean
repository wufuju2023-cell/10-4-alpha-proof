import Mathlib

namespace FateCurriculum

/-- FATE-M #61, curriculum variant 51/200; tier 2: packed_target. -/
theorem fate_m_061_v051 {G : Type*} [Group G]
    (M : Subgroup G) [hM : M.Normal] (N : Subgroup G) [hN: N.Normal]
    (curriculum_pack : (((205101 : Nat) = 205101) ∧ (((205102 : Nat) = 205102) ∧ (((205103 : Nat) = 205103) ∧ (((205104 : Nat) = 205104) ∧ (((205105 : Nat) = 205105) ∧ (((205106 : Nat) = 205106) ∧ (((205107 : Nat) = 205107) ∧ (((205108 : Nat) = 205108) ∧ (((205109 : Nat) = 205109) ∧ (((205110 : Nat) = 205110) ∧ (((205111 : Nat) = 205111) ∧ ((M ⊓ N).Normal))))))))))))) :
    ((M ⊓ N).Normal) := by
  sorry

end FateCurriculum

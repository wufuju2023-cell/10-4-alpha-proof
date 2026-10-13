import Mathlib

namespace FateCurriculum

/-- FATE-M #52, curriculum variant 46/200; tier 2: packed_target. -/
theorem fate_m_052_v046 {G : Type*} [Group G] (a b c : G) (h : a * b * c = 1)
    (curriculum_pack : (((204601 : Nat) = 204601) ∧ (((204602 : Nat) = 204602) ∧ (((204603 : Nat) = 204603) ∧ (((204604 : Nat) = 204604) ∧ (((204605 : Nat) = 204605) ∧ (((204606 : Nat) = 204606) ∧ (b * c * a = 1)))))))) :
    (b * c * a = 1) := by
  sorry

end FateCurriculum

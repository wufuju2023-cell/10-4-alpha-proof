import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 48/200; tier 2: packed_target. -/
theorem fate_m_009_v048 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((204801 : Nat) = 204801) ∧ (((204802 : Nat) = 204802) ∧ (((204803 : Nat) = 204803) ∧ (((204804 : Nat) = 204804) ∧ (((204805 : Nat) = 204805) ∧ (((204806 : Nat) = 204806) ∧ (((204807 : Nat) = 204807) ∧ (((204808 : Nat) = 204808) ∧ (orderOf a = 0)))))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

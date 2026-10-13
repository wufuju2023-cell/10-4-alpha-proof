import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 53/200; tier 2: packed_target. -/
theorem fate_m_009_v053 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_pack : (((205301 : Nat) = 205301) ∧ (((205302 : Nat) = 205302) ∧ (((205303 : Nat) = 205303) ∧ (((205304 : Nat) = 205304) ∧ (((205305 : Nat) = 205305) ∧ (((205306 : Nat) = 205306) ∧ (((205307 : Nat) = 205307) ∧ (((205308 : Nat) = 205308) ∧ (((205309 : Nat) = 205309) ∧ (((205310 : Nat) = 205310) ∧ (((205311 : Nat) = 205311) ∧ (((205312 : Nat) = 205312) ∧ (((205313 : Nat) = 205313) ∧ (orderOf a = 0))))))))))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

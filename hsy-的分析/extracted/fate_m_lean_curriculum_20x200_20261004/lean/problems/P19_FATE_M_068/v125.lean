import Mathlib

namespace FateCurriculum

/-- FATE-M #68, curriculum variant 125/200; tier 4: disjunction_elimination. -/
theorem fate_m_068_v125 {G : Type*} [Group G] (a : G)
    (curriculum_cases : ((orderOf a = orderOf (a⁻¹)) ∨ ((((412501 : Nat) = 412501) ∧ False) ∨ ((((412502 : Nat) = 412502) ∧ False) ∨ ((((412503 : Nat) = 412503) ∧ False) ∨ ((((412504 : Nat) = 412504) ∧ False) ∨ (((412505 : Nat) = 412505) ∧ False))))))) :
    (orderOf a = orderOf (a⁻¹)) := by
  sorry

end FateCurriculum

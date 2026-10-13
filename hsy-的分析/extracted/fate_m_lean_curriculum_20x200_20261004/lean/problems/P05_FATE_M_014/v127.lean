import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 127/200; tier 4: disjunction_elimination. -/
theorem fate_m_014_v127 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0)
    (curriculum_cases : ((a = 0) ∨ ((((412701 : Nat) = 412701) ∧ False) ∨ ((((412702 : Nat) = 412702) ∧ False) ∨ ((((412703 : Nat) = 412703) ∧ False) ∨ ((((412704 : Nat) = 412704) ∧ False) ∨ ((((412705 : Nat) = 412705) ∧ False) ∨ ((((412706 : Nat) = 412706) ∧ False) ∨ (((412707 : Nat) = 412707) ∧ False))))))))) :
    (a = 0) := by
  sorry

end FateCurriculum

import Mathlib

namespace FateCurriculum

/-- FATE-M #14, curriculum variant 180/200; tier 6: unscaffolded. -/
theorem fate_m_014_v180 {R : Type*} [Ring R] [IsDomain R] (a : R) (n : ℕ)
    (eq : a ^ n = 0) :
    ((a = 0) ∧ (((618001 : Nat) = 618001) ∧ (((618002 : Nat) = 618002) ∧ (((618003 : Nat) = 618003) ∧ ((618004 : Nat) = 618004))))) := by
  sorry

end FateCurriculum

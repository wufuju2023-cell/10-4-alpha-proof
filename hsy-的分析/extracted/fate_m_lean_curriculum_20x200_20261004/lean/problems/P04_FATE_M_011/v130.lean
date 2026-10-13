import Mathlib

namespace FateCurriculum

/-- FATE-M #11, curriculum variant 130/200; tier 4: disjunction_elimination. -/
theorem fate_m_011_v130 {R : Type*} [Ring R] (a b c : R)
    (curriculum_cases : ((a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) ∨ ((((413001 : Nat) = 413001) ∧ False) ∨ ((((413002 : Nat) = 413002) ∧ False) ∨ ((((413003 : Nat) = 413003) ∧ False) ∨ ((((413004 : Nat) = 413004) ∧ False) ∨ ((((413005 : Nat) = 413005) ∧ False) ∨ ((((413006 : Nat) = 413006) ∧ False) ∨ ((((413007 : Nat) = 413007) ∧ False) ∨ ((((413008 : Nat) = 413008) ∧ False) ∨ ((((413009 : Nat) = 413009) ∧ False) ∨ (((413010 : Nat) = 413010) ∧ False)))))))))))) :
    (a * (b - c) = a * b - a * c ∧ (b - c) * a = b * a - c * a) := by
  sorry

end FateCurriculum

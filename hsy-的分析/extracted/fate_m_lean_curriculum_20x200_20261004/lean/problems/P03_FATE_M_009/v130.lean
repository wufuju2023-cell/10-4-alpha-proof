import Mathlib

namespace FateCurriculum

/-- FATE-M #9, curriculum variant 130/200; tier 4: disjunction_elimination. -/
theorem fate_m_009_v130 {G H : Type*} [Group G] [Group H] {f : G →* H} {a : G}
    (h : orderOf (f a) = 0)
    (curriculum_cases : ((orderOf a = 0) ∨ ((((413001 : Nat) = 413001) ∧ False) ∨ ((((413002 : Nat) = 413002) ∧ False) ∨ ((((413003 : Nat) = 413003) ∧ False) ∨ ((((413004 : Nat) = 413004) ∧ False) ∨ ((((413005 : Nat) = 413005) ∧ False) ∨ ((((413006 : Nat) = 413006) ∧ False) ∨ ((((413007 : Nat) = 413007) ∧ False) ∨ ((((413008 : Nat) = 413008) ∧ False) ∨ ((((413009 : Nat) = 413009) ∧ False) ∨ (((413010 : Nat) = 413010) ∧ False)))))))))))) :
    (orderOf a = 0) := by
  sorry

end FateCurriculum

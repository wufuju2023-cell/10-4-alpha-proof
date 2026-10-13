import Mathlib

namespace FateCurriculum

/-- FATE-M #18, curriculum variant 125/200; tier 4: disjunction_elimination. -/
theorem fate_m_018_v125 {R : Type*} [CommRing R] (I : Ideal R) (J : Ideal R) (K : Ideal R)
    (h₁ : I ≤ K) (h₂ : J ≤ K)
    (curriculum_cases : ((I + J ≤ K) ∨ ((((412501 : Nat) = 412501) ∧ False) ∨ ((((412502 : Nat) = 412502) ∧ False) ∨ ((((412503 : Nat) = 412503) ∧ False) ∨ ((((412504 : Nat) = 412504) ∧ False) ∨ (((412505 : Nat) = 412505) ∧ False))))))) :
    (I + J ≤ K) := by
  sorry

end FateCurriculum

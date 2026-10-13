import Mathlib

namespace FateCurriculum

/-- FATE-M #18, curriculum variant 4/200; tier 1: direct_target. -/
theorem fate_m_018_v004 {R : Type*} [CommRing R] (I : Ideal R) (J : Ideal R) (K : Ideal R)
    (h₁ : I ≤ K) (h₂ : J ≤ K)
    (curriculum_target : (I + J ≤ K))
    (curriculum_context_001 : ((100401 : Nat) = 100401))
    (curriculum_context_002 : ((100402 : Nat) = 100402))
    (curriculum_context_003 : ((100403 : Nat) = 100403)) :
    (I + J ≤ K) := by
  sorry

end FateCurriculum

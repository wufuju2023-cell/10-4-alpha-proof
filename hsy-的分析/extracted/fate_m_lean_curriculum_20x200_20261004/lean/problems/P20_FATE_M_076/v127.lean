import Mathlib

open Pointwise

namespace FateCurriculum

/-- FATE-M #76, curriculum variant 127/200; tier 4: disjunction_elimination. -/
theorem fate_m_076_v127 {G : Type*} [Group G] (H : Subgroup G) (a b : G)
    (curriculum_cases : (((a • (H : Set G)) ∩ (b • (H : Set G)) ≠ ∅ →
    QuotientGroup.mk (s := H) a = QuotientGroup.mk (s := H) b) ∨ ((((412701 : Nat) = 412701) ∧ False) ∨ ((((412702 : Nat) = 412702) ∧ False) ∨ ((((412703 : Nat) = 412703) ∧ False) ∨ ((((412704 : Nat) = 412704) ∧ False) ∨ ((((412705 : Nat) = 412705) ∧ False) ∨ ((((412706 : Nat) = 412706) ∧ False) ∨ (((412707 : Nat) = 412707) ∧ False))))))))) :
    ((a • (H : Set G)) ∩ (b • (H : Set G)) ≠ ∅ →
    QuotientGroup.mk (s := H) a = QuotientGroup.mk (s := H) b) := by
  sorry

end FateCurriculum

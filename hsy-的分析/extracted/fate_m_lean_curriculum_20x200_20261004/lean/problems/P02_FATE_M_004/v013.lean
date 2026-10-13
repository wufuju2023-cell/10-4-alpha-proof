import Mathlib

namespace FateCurriculum

/-- FATE-M #4, curriculum variant 13/200; tier 1: direct_target. -/
theorem fate_m_004_v013 {G G' : Type*} [Group G] [Group G'] (f : G →* G') {a b : G}
    (curriculum_target : ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)))
    (curriculum_context_001 : ((101301 : Nat) = 101301))
    (curriculum_context_002 : ((101302 : Nat) = 101302))
    (curriculum_context_003 : ((101303 : Nat) = 101303))
    (curriculum_context_004 : ((101304 : Nat) = 101304))
    (curriculum_context_005 : ((101305 : Nat) = 101305))
    (curriculum_context_006 : ((101306 : Nat) = 101306))
    (curriculum_context_007 : ((101307 : Nat) = 101307))
    (curriculum_context_008 : ((101308 : Nat) = 101308))
    (curriculum_context_009 : ((101309 : Nat) = 101309))
    (curriculum_context_010 : ((101310 : Nat) = 101310))
    (curriculum_context_011 : ((101311 : Nat) = 101311))
    (curriculum_context_012 : ((101312 : Nat) = 101312)) :
    ((a * b ∈ f.ker)  ↔ (b * a ∈ f.ker)) := by
  sorry

end FateCurriculum

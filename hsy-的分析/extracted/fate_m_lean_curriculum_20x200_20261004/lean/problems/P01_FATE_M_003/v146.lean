import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 146/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v146 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((414601 : Nat) = 414601) ∧ False) ∨ ((((414602 : Nat) = 414602) ∧ False) ∨ ((((414603 : Nat) = 414603) ∧ False) ∨ ((((414604 : Nat) = 414604) ∧ False) ∨ ((((414605 : Nat) = 414605) ∧ False) ∨ ((((414606 : Nat) = 414606) ∧ False) ∨ ((((414607 : Nat) = 414607) ∧ False) ∨ ((((414608 : Nat) = 414608) ∧ False) ∨ ((((414609 : Nat) = 414609) ∧ False) ∨ ((((414610 : Nat) = 414610) ∧ False) ∨ ((((414611 : Nat) = 414611) ∧ False) ∨ ((((414612 : Nat) = 414612) ∧ False) ∨ ((((414613 : Nat) = 414613) ∧ False) ∨ ((((414614 : Nat) = 414614) ∧ False) ∨ ((((414615 : Nat) = 414615) ∧ False) ∨ ((((414616 : Nat) = 414616) ∧ False) ∨ ((((414617 : Nat) = 414617) ∧ False) ∨ ((((414618 : Nat) = 414618) ∧ False) ∨ ((((414619 : Nat) = 414619) ∧ False) ∨ ((((414620 : Nat) = 414620) ∧ False) ∨ ((((414621 : Nat) = 414621) ∧ False) ∨ ((((414622 : Nat) = 414622) ∧ False) ∨ ((((414623 : Nat) = 414623) ∧ False) ∨ ((((414624 : Nat) = 414624) ∧ False) ∨ ((((414625 : Nat) = 414625) ∧ False) ∨ (((414626 : Nat) = 414626) ∧ False)))))))))))))))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

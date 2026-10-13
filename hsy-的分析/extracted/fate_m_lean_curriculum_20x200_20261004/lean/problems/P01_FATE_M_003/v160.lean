import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 160/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v160 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((416001 : Nat) = 416001) ∧ False) ∨ ((((416002 : Nat) = 416002) ∧ False) ∨ ((((416003 : Nat) = 416003) ∧ False) ∨ ((((416004 : Nat) = 416004) ∧ False) ∨ ((((416005 : Nat) = 416005) ∧ False) ∨ ((((416006 : Nat) = 416006) ∧ False) ∨ ((((416007 : Nat) = 416007) ∧ False) ∨ ((((416008 : Nat) = 416008) ∧ False) ∨ ((((416009 : Nat) = 416009) ∧ False) ∨ ((((416010 : Nat) = 416010) ∧ False) ∨ ((((416011 : Nat) = 416011) ∧ False) ∨ ((((416012 : Nat) = 416012) ∧ False) ∨ ((((416013 : Nat) = 416013) ∧ False) ∨ ((((416014 : Nat) = 416014) ∧ False) ∨ ((((416015 : Nat) = 416015) ∧ False) ∨ ((((416016 : Nat) = 416016) ∧ False) ∨ ((((416017 : Nat) = 416017) ∧ False) ∨ ((((416018 : Nat) = 416018) ∧ False) ∨ ((((416019 : Nat) = 416019) ∧ False) ∨ ((((416020 : Nat) = 416020) ∧ False) ∨ ((((416021 : Nat) = 416021) ∧ False) ∨ ((((416022 : Nat) = 416022) ∧ False) ∨ ((((416023 : Nat) = 416023) ∧ False) ∨ ((((416024 : Nat) = 416024) ∧ False) ∨ ((((416025 : Nat) = 416025) ∧ False) ∨ ((((416026 : Nat) = 416026) ∧ False) ∨ ((((416027 : Nat) = 416027) ∧ False) ∨ ((((416028 : Nat) = 416028) ∧ False) ∨ ((((416029 : Nat) = 416029) ∧ False) ∨ ((((416030 : Nat) = 416030) ∧ False) ∨ ((((416031 : Nat) = 416031) ∧ False) ∨ ((((416032 : Nat) = 416032) ∧ False) ∨ ((((416033 : Nat) = 416033) ∧ False) ∨ ((((416034 : Nat) = 416034) ∧ False) ∨ ((((416035 : Nat) = 416035) ∧ False) ∨ ((((416036 : Nat) = 416036) ∧ False) ∨ ((((416037 : Nat) = 416037) ∧ False) ∨ ((((416038 : Nat) = 416038) ∧ False) ∨ ((((416039 : Nat) = 416039) ∧ False) ∨ (((416040 : Nat) = 416040) ∧ False)))))))))))))))))))))))))))))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

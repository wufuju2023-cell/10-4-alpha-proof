import Mathlib

namespace FateCurriculum

/-- FATE-M #3, curriculum variant 152/200; tier 4: disjunction_elimination. -/
theorem fate_m_003_v152 {G H K : Type*} [Group G] [Group H] [Group K]
    (f : G →* H) (g : H →* K) (hf : Function.Surjective f) (hg : Function.Surjective g)
    (curriculum_cases : ((Function.Surjective (g.comp f)) ∨ ((((415201 : Nat) = 415201) ∧ False) ∨ ((((415202 : Nat) = 415202) ∧ False) ∨ ((((415203 : Nat) = 415203) ∧ False) ∨ ((((415204 : Nat) = 415204) ∧ False) ∨ ((((415205 : Nat) = 415205) ∧ False) ∨ ((((415206 : Nat) = 415206) ∧ False) ∨ ((((415207 : Nat) = 415207) ∧ False) ∨ ((((415208 : Nat) = 415208) ∧ False) ∨ ((((415209 : Nat) = 415209) ∧ False) ∨ ((((415210 : Nat) = 415210) ∧ False) ∨ ((((415211 : Nat) = 415211) ∧ False) ∨ ((((415212 : Nat) = 415212) ∧ False) ∨ ((((415213 : Nat) = 415213) ∧ False) ∨ ((((415214 : Nat) = 415214) ∧ False) ∨ ((((415215 : Nat) = 415215) ∧ False) ∨ ((((415216 : Nat) = 415216) ∧ False) ∨ ((((415217 : Nat) = 415217) ∧ False) ∨ ((((415218 : Nat) = 415218) ∧ False) ∨ ((((415219 : Nat) = 415219) ∧ False) ∨ ((((415220 : Nat) = 415220) ∧ False) ∨ ((((415221 : Nat) = 415221) ∧ False) ∨ ((((415222 : Nat) = 415222) ∧ False) ∨ ((((415223 : Nat) = 415223) ∧ False) ∨ ((((415224 : Nat) = 415224) ∧ False) ∨ ((((415225 : Nat) = 415225) ∧ False) ∨ ((((415226 : Nat) = 415226) ∧ False) ∨ ((((415227 : Nat) = 415227) ∧ False) ∨ ((((415228 : Nat) = 415228) ∧ False) ∨ ((((415229 : Nat) = 415229) ∧ False) ∨ ((((415230 : Nat) = 415230) ∧ False) ∨ ((((415231 : Nat) = 415231) ∧ False) ∨ (((415232 : Nat) = 415232) ∧ False)))))))))))))))))))))))))))))))))) :
    (Function.Surjective (g.comp f)) := by
  sorry

end FateCurriculum

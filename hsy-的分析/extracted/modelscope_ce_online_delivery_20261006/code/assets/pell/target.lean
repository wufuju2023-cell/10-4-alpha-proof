/-
  佩尔题原目标（摘录自用户已验收实验 20260828-real7b-pell-success）。
  来源：reap-new-update-model/v1-result/20260828-real7b-pell-success/
        inputs/original-target/student/StudentDeclarations.lean（Pell 命名空间）
  题面：x² - 2y² = 1 有任意大的正整数解（两坐标同时超过任意给定界）。
  说明：本文件仅作测试题面使用，不声称原创；完整证明与独立验收见原实验包。
-/
import Mathlib

namespace CodexMathFive.Pell

def step (p : ℕ × ℕ) : ℕ × ℕ :=
  (3 * p.1 + 4 * p.2, 2 * p.1 + 3 * p.2)

def pellPair : ℕ → ℕ × ℕ
  | 0 => (3, 2)
  | k + 1 => step (pellPair k)

def Target : Prop :=
  ∀ B : ℕ, ∃ x y : ℕ, B < x ∧ B < y ∧ x ^ 2 = 2 * y ^ 2 + 1

end CodexMathFive.Pell

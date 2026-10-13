/-
  佩尔题（原始 Target）真实 Lean 冒烟：在 Lean 4.28 内核里运行对齐后的 MCTS。

  前置（见同目录 README.md）：
  - /mnt/gloway/projects/lean-4.28-reap/runtime 已构建（含 lean/patches 对齐补丁）；
  - policy/value 端点可由 mock 服务或 GPU 策略服务提供。

  预期：
  - mock 服务（只会回 "trivial"）→ 搜索 exhausted，但 progress/result/observer JSONL 应齐全；
  - 真实策略服务 → 应返回 solved 并输出 proof_script。
-/
import ReapRuntime

set_option reap.policy_endpoint "http://127.0.0.1:18080/v1"
set_option reap.value_endpoint "http://127.0.0.1:18080/v1"
set_option reap.num_samples 2
set_option reap.num_premises 0
set_option reap.max_goals 16
set_option reap.max_steps 16

namespace CodexMathFive.PellSmoke

/-- 原题：x² - 2y² = 1 有任意大的正整数解（两坐标同时超过任意界）。 -/
def Target : Prop :=
  ∀ B : ℕ, ∃ x y : ℕ, B < x ∧ B < y ∧ x ^ 2 = 2 * y ^ 2 + 1

theorem pell_smoke : Target := by
  reapTrainingMCTS

end CodexMathFive.PellSmoke

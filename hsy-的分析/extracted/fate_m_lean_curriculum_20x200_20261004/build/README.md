# Build

`lean-validation/` 保存本次全量 Lean 编译的派生报告与每个家族日志。Mathlib 的 `.lake/` 依赖缓存不属于交付，按需由 `lake update` 和 `lake exe cache get` 重建。

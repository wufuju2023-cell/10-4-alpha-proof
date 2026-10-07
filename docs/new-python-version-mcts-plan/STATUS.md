# STATUS · lean-mcts-cpu-server v0 已落地（2026-10-07）

实现仓库：**https://github.com/wufuju2023-cell/lean-mcts-cpu-server**（本机路径 `/mnt/gloway/projects/lean-mcts-cpu-server`；
全新机器一键安装见该仓库 `docs/FRESH-MACHINE.md`，环境快照与安装脚本在 `env/`）

已完成（对应 07 里程碑）：
- **M0 基准**：延迟/扩展/波次全部实测，见实现仓库 `docs/BENCH-2026-10-07.md`；
- **M1 walking skeleton**：Pell 第一课端到端 SOLVED + 内核复验（`python3 -m lmc.cli smoke` / `mcts-smoke` 均 PASS）；
- **M2 池化并发（部分）**：N=1→12 线性扩展（10.3→69 applies/s），20 题波次 45.1 题/min；
  watchdog 类故障自愈尚未实现（下一步）。

与规格的偏差（实现中发现的真实约束）：
1. `Tactic.SavedState` 在 check 模式下的输入：`IO.getStdin` 指向空流，需 re-open `/proc/self/fd/0`；
2. 会话输出：elaborator task 线程 stdout 被缓冲，需改走 FIFO（`LMC_OUTPUT_FIFO`）；
3. 每个会话目前一题一进程（shell 文件 = 题目 + 会话循环），进程池预热复用未做。

下一步建议（按 07 里程碑顺序）：
- M2 收尾：watchdog/自动重启、`lmc status`；
- M3：接入 GPU policy/value（`/eval` 契约），真模型小波次；
- M4：轨迹仓库 + export（`update_offline` 冒烟）；
- 优化：进程池预热（把 6s 启动成本摊掉）、`elapsed` 的 ns 精度。

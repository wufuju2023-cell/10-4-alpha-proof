# 既有 Online-v2 交付审计

审计输入：

- `experiments/online_v2_teacher_distill_20261004/build/online_v2_teacher_distill_20261004_delivery.zip`
- SHA-256：`7a78c053572610eeedefa5ee9b7c679d86cc4e3a529b109ded07316e4ddb6c50`
- 原交付本机结果：8 项单测与合成 smoke 通过；无真实模型/Lean 结论。

原交付已经做对的部分是：冻结 behavior wave、拒绝混合 policy version、按独立 problem
触发、完整词表 conditional KL、KL 硬门、optimizer/RNG 回滚和教师候选严格 proof-path
过滤。这些语义在本工作流中保留。

为当前 A/B 实验必须修正的部分：

1. 原目标逐 token 分别 clipping，但文档把 tactic 定义成一个序列 action。现在改为 action
   token log-ratio 求和后形成唯一 sequence ratio，再 clipping。
2. 原 baseline 是同 state 候选 Q 的简单均值；现在用采样 behavior probability 加权，并把
   baseline 与 action value 一起持久化，明确 advantage=`Q-b`。
3. 原 buffer 虽按 problem 达到 readiness，但 learner 仍按 event 等权；一个长轨迹可以支配
   梯度。现在每个 problem 总权重相同，并限制单 problem/trajectory 事件数。
4. 原实现只锚到 wave behavior；现在另加 immutable base anchor，避免多个小 wave 的累计
   漂移。二者都不是采样 action 的 `Δ²`，而是 sampled prefix 上的完整词表 forward KL。
5. 原实现信任回执中的 old logprob；现在用冻结 behavior 重算逐 token logprob，误差超过
   `5e-4` 时在 optimizer step 前拒绝。
6. 原 Online-v2 learner 没有真正更新共同定义的 CE64 value head；现在支持只在严格验证
   solution path 上训练剩余步数，权重固定为 `1e-3`。
7. 原快照复制整个 `state_dict`，对 7B 不经济；现在只复制 trainable 参数，并补齐
   scheduler、NumPy RNG、KL beta 和 step counter。
8. 原 rollout schema 没有足以强制正奖励验证的字段；现在任何正 local reward 都必须同时
   有 proof/disproof 状态、`terminal_verified=true` 和非空 receipt id。timeout 与基础设施
   错误固定为零。

剩余风险没有被单测消除：sequence ratio 随 tactic 长度方差增大；完整词表 KL 会增加显存
与算力；同 state 候选集合是搜索截断后的有限集合。协议通过短 update epoch、双 KL 门、
problem 平衡和真实 smoke 控制风险，但不能在运行前声称效果优于 CE。

"""离线回放与批构造（官方式专家迭代）。

官方设定（N33 B3/A5）：
- actor 把整局搜索轨迹写进 replay buffer，learner 批量训练；
- policy 目标 = 对“搜索选中动作”做交叉熵；
- value 目标 = compute_value_target 的 64 桶 CE；
- SFT 混比 10%；replay 含证明 + 反证，超时样本不进训练。
"""

from __future__ import annotations

import random
from typing import Iterable, List, Sequence

from alphaproof.config import TrainConfig
from alphaproof.data.events import Trajectory, Transition


def sample_replay(replay: Sequence[Transition], batch_size: int,
                  rng: random.Random | None = None) -> List[Transition]:
    rng = rng or random.Random(0)
    if not replay:
        return []
    if len(replay) <= batch_size:
        return list(replay)
    return rng.sample(list(replay), batch_size)


class OfflineBatchBuilder:
    """构造一次专家迭代更新用的 batch：replay(含反证) + sft 混比。"""

    def __init__(self, config: TrainConfig | None = None, seed: int = 0) -> None:
        self.config = config or TrainConfig()
        self.rng = random.Random(seed)

    def ingest(self, trajectories: Iterable[Trajectory], replay: List[Transition]) -> dict:
        """把轨迹按官方规则并入回放：proof/disproof 收，timeout 按配置。"""
        stats = {"proof": 0, "disproof": 0, "timeout": 0, "skipped": 0}
        for traj in trajectories:
            kind = traj.kind
            if kind == "timeout" and not self.config.include_timeout:
                stats["skipped"] += 1
                continue
            if kind == "disproof" and not self.config.include_disproof:
                stats["skipped"] += 1
                continue
            stats[kind] = stats.get(kind, 0) + len(traj.transitions)
            replay.extend(traj.transitions)
        return stats

    def build(self, replay: Sequence[Transition], batch_size: int,
              sft_pool: Sequence[Transition] | None = None) -> List[Transition]:
        """replay 与 SFT 按 sft_mix 混比（SFT 只贡献 policy CE，无 value target）。"""
        cfg = self.config
        n_sft = int(round(batch_size * cfg.sft_mix)) if sft_pool else 0
        n_replay = batch_size - n_sft
        batch = sample_replay(replay, n_replay, self.rng)
        if n_sft > 0:
            batch += sample_replay(sft_pool, n_sft, self.rng)  # type: ignore[arg-type]
        return batch

    def weights(self, batch: Sequence[Transition]) -> List[float]:
        """样本损失权重：反证可按 disproof_weight 调整，SFT 同理为 1。"""
        return [self.config.disproof_weight if t.kind == "disproof" else 1.0 for t in batch]

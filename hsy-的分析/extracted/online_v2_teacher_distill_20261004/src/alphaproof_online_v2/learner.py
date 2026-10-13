from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import math
import random
from typing import Callable, Iterable

import torch
from torch import nn

from .objectives import clipped_ppo_loss, exact_forward_kl, masked_entropy, token_logprobs
from .schema import RolloutSample


@dataclass
class OnlineV2Config:
    clip_epsilon: float = 0.2
    initial_kl_beta: float = 0.02
    target_kl: float = 0.02
    hard_kl_limit: float = 0.08
    entropy_coef: float = 0.0
    max_grad_norm: float = 1.0
    update_epochs: int = 2
    min_independent_problems: int = 2
    pad_token_id: int = 0

    def validate(self) -> None:
        if not 0.0 < self.clip_epsilon < 1.0:
            raise ValueError("clip_epsilon must be in (0,1)")
        if self.initial_kl_beta < 0 or self.target_kl <= 0:
            raise ValueError("KL settings must be non-negative/positive")
        if self.hard_kl_limit <= self.target_kl:
            raise ValueError("hard_kl_limit must exceed target_kl")
        if self.max_grad_norm <= 0 or self.update_epochs < 1:
            raise ValueError("gradient norm and update epochs must be positive")
        if self.min_independent_problems < 1:
            raise ValueError("min_independent_problems must be positive")


@dataclass(frozen=True)
class UpdateReceipt:
    accepted: bool
    reason: str
    policy_version: str
    samples: int
    independent_problems: int
    optimizer_steps: int
    ppo_loss: float
    exact_kl: float
    approx_kl: float
    clip_fraction: float
    entropy: float
    grad_norm: float
    kl_beta_before: float
    kl_beta_after: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _Snapshot:
    model: dict[str, torch.Tensor]
    optimizer: dict
    python_rng: object
    torch_rng: torch.Tensor
    cuda_rng: list[torch.Tensor] | None
    optimizer_steps: int


def _extract_logits(output: object) -> torch.Tensor:
    if isinstance(output, torch.Tensor):
        return output
    logits = getattr(output, "logits", None)
    if isinstance(logits, torch.Tensor):
        return logits
    if isinstance(output, dict) and isinstance(output.get("logits"), torch.Tensor):
        return output["logits"]
    raise TypeError("policy model output must be a tensor or expose .logits")


class OnlineV2Learner:
    """Version-bound clipped PPO with exact KL and transactional rollback.

    The reference model must be the frozen behavior policy that generated the
    supplied wave. A fixed base-model anchor can be added by the caller as a
    second regularizer, but is deliberately not conflated with the PPO old
    policy here.
    """

    def __init__(
        self,
        policy_model: nn.Module,
        reference_model: nn.Module,
        optimizer: torch.optim.Optimizer,
        *,
        config: OnlineV2Config | None = None,
        device: str | torch.device = "cpu",
        canary: Callable[[nn.Module], bool] | None = None,
    ) -> None:
        self.policy_model = policy_model
        self.reference_model = reference_model
        self.optimizer = optimizer
        self.config = config or OnlineV2Config()
        self.config.validate()
        self.device = torch.device(device)
        self.canary = canary
        self.kl_beta = self.config.initial_kl_beta
        self.optimizer_steps = 0
        self.reference_model.eval()
        for parameter in self.reference_model.parameters():
            parameter.requires_grad_(False)

    def update(self, samples: Iterable[RolloutSample], *, policy_version: str) -> UpdateReceipt:
        wave = list(samples)
        if not wave:
            raise ValueError("cannot update from an empty wave")
        for sample in wave:
            sample.validate()
            if sample.policy_version != policy_version:
                raise ValueError("wave contains a stale or mixed policy version")
        independent = len({sample.problem_id for sample in wave})
        if independent < self.config.min_independent_problems:
            raise ValueError(
                f"requires {self.config.min_independent_problems} independent problems, got {independent}"
            )

        snapshot = self._snapshot()
        beta_before = self.kl_beta
        metrics = {
            "ppo_loss": 0.0,
            "exact_kl": 0.0,
            "approx_kl": 0.0,
            "clip_fraction": 0.0,
            "entropy": 0.0,
            "grad_norm": 0.0,
        }
        batch = self._collate(wave)
        self.policy_model.train()
        try:
            for _ in range(self.config.update_epochs):
                self.optimizer.zero_grad(set_to_none=True)
                policy_logits = _extract_logits(
                    self.policy_model(
                        input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
                    )
                )
                with torch.no_grad():
                    reference_logits = _extract_logits(
                        self.reference_model(
                            input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
                        )
                    )
                new_logprobs = token_logprobs(policy_logits, batch["input_ids"])
                ppo = clipped_ppo_loss(
                    new_logprobs,
                    batch["old_logprobs"][:, 1:],
                    batch["action_mask"][:, 1:],
                    batch["advantages"],
                    self.config.clip_epsilon,
                )
                exact_kl = exact_forward_kl(
                    policy_logits, reference_logits, batch["action_mask"]
                )
                entropy = masked_entropy(policy_logits, batch["action_mask"])
                if float(exact_kl.detach().cpu()) > self.config.hard_kl_limit:
                    return self._rollback_receipt(
                        snapshot,
                        reason="pre_step_hard_kl_limit",
                        policy_version=policy_version,
                        samples=len(wave),
                        independent=independent,
                        beta_before=beta_before,
                        metrics=metrics,
                    )
                total = ppo.loss + self.kl_beta * exact_kl - self.config.entropy_coef * entropy
                if not bool(torch.isfinite(total).item()):
                    raise FloatingPointError("non-finite online objective")
                total.backward()
                parameters = [
                    parameter
                    for parameter in self.policy_model.parameters()
                    if parameter.requires_grad
                ]
                grad_norm = torch.nn.utils.clip_grad_norm_(
                    parameters, self.config.max_grad_norm
                )
                if not bool(torch.isfinite(torch.as_tensor(grad_norm)).item()):
                    raise FloatingPointError("non-finite gradient norm")
                self.optimizer.step()
                self.optimizer_steps += 1
                if not all(torch.isfinite(parameter).all() for parameter in parameters):
                    raise FloatingPointError("optimizer produced non-finite parameters")
                metrics = {
                    "ppo_loss": float(ppo.loss.detach().cpu()),
                    "exact_kl": float(exact_kl.detach().cpu()),
                    "approx_kl": float(ppo.approx_kl.detach().cpu()),
                    "clip_fraction": float(ppo.clip_fraction.detach().cpu()),
                    "entropy": float(entropy.detach().cpu()),
                    "grad_norm": float(torch.as_tensor(grad_norm).detach().cpu()),
                }

            post_kl = self._measure_exact_kl(batch)
            metrics["exact_kl"] = post_kl
            if not math.isfinite(post_kl) or post_kl > self.config.hard_kl_limit:
                return self._rollback_receipt(
                    snapshot,
                    reason="post_step_hard_kl_limit",
                    policy_version=policy_version,
                    samples=len(wave),
                    independent=independent,
                    beta_before=beta_before,
                    metrics=metrics,
                )
            if self.canary is not None and not bool(self.canary(self.policy_model)):
                return self._rollback_receipt(
                    snapshot,
                    reason="canary_rejected",
                    policy_version=policy_version,
                    samples=len(wave),
                    independent=independent,
                    beta_before=beta_before,
                    metrics=metrics,
                )
            self._adapt_beta(post_kl)
            self.policy_model.eval()
            return UpdateReceipt(
                accepted=True,
                reason="accepted",
                policy_version=policy_version,
                samples=len(wave),
                independent_problems=independent,
                optimizer_steps=self.optimizer_steps,
                kl_beta_before=beta_before,
                kl_beta_after=self.kl_beta,
                **metrics,
            )
        except Exception:
            self._restore(snapshot)
            self.policy_model.eval()
            raise

    def _collate(self, samples: list[RolloutSample]) -> dict[str, torch.Tensor]:
        length = max(sample.input_ids.numel() for sample in samples)
        batch_size = len(samples)
        input_ids = torch.full(
            (batch_size, length), self.config.pad_token_id, dtype=torch.long, device=self.device
        )
        attention_mask = torch.zeros((batch_size, length), dtype=torch.long, device=self.device)
        action_mask = torch.zeros((batch_size, length), dtype=torch.bool, device=self.device)
        old_logprobs = torch.zeros((batch_size, length), dtype=torch.float32, device=self.device)
        advantages = torch.empty((batch_size,), dtype=torch.float32, device=self.device)
        for row, sample in enumerate(samples):
            size = sample.input_ids.numel()
            input_ids[row, :size] = sample.input_ids.to(self.device, dtype=torch.long)
            attention_mask[row, :size] = sample.attention_mask.to(self.device, dtype=torch.long)
            action_mask[row, :size] = sample.action_mask.to(self.device, dtype=torch.bool)
            old_logprobs[row, :size] = sample.old_logprobs.to(self.device, dtype=torch.float32)
            advantages[row] = float(sample.advantage)
        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "action_mask": action_mask,
            "old_logprobs": old_logprobs,
            "advantages": advantages,
        }

    @torch.no_grad()
    def _measure_exact_kl(self, batch: dict[str, torch.Tensor]) -> float:
        policy_logits = _extract_logits(
            self.policy_model(
                input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
            )
        )
        reference_logits = _extract_logits(
            self.reference_model(
                input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
            )
        )
        value = exact_forward_kl(policy_logits, reference_logits, batch["action_mask"])
        return float(value.detach().cpu())

    def _adapt_beta(self, observed_kl: float) -> None:
        if observed_kl > 1.5 * self.config.target_kl:
            self.kl_beta = min(100.0, max(1e-8, self.kl_beta * 2.0))
        elif observed_kl < self.config.target_kl / 1.5:
            self.kl_beta = max(1e-8, self.kl_beta / 2.0)

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            model={key: value.detach().clone() for key, value in self.policy_model.state_dict().items()},
            optimizer=copy.deepcopy(self.optimizer.state_dict()),
            python_rng=random.getstate(),
            torch_rng=torch.random.get_rng_state().clone(),
            cuda_rng=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            optimizer_steps=self.optimizer_steps,
        )

    def _restore(self, snapshot: _Snapshot) -> None:
        self.policy_model.load_state_dict(snapshot.model)
        self.optimizer.load_state_dict(snapshot.optimizer)
        random.setstate(snapshot.python_rng)
        torch.random.set_rng_state(snapshot.torch_rng)
        if snapshot.cuda_rng is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(snapshot.cuda_rng)
        self.optimizer_steps = snapshot.optimizer_steps

    def _rollback_receipt(
        self,
        snapshot: _Snapshot,
        *,
        reason: str,
        policy_version: str,
        samples: int,
        independent: int,
        beta_before: float,
        metrics: dict[str, float],
    ) -> UpdateReceipt:
        self._restore(snapshot)
        self.kl_beta = beta_before
        self.policy_model.eval()
        return UpdateReceipt(
            accepted=False,
            reason=reason,
            policy_version=policy_version,
            samples=samples,
            independent_problems=independent,
            optimizer_steps=self.optimizer_steps,
            kl_beta_before=beta_before,
            kl_beta_after=self.kl_beta,
            **metrics,
        )

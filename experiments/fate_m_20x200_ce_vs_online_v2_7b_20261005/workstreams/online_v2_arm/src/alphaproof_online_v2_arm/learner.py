from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import math
from pathlib import Path
import random
from typing import Callable, Mapping, Protocol

import torch
from torch import nn

from .adapter_reference import SingleBackboneAdapterReferences
from .buffer import problem_balanced_weights
from .builder import ValidatedRolloutWave
from .ledger import TransactionHandle, WaveTransactionStore
from .objectives import (
    selected_action_logits,
    selected_exact_forward_kl,
    selected_masked_entropy,
    selected_warped_token_logprobs,
    sequence_clipped_ppo_loss,
    weighted_categorical_value_loss,
)
from .schema import RolloutSample


TRAINING_STATE_SCHEMA = "fate.online_v2.cross_wave_training_state.v1"


@dataclass
class OnlineV2Config:
    clip_epsilon: float = 0.2
    behavior_kl_beta: float = 0.02
    anchor_kl_beta: float = 0.005
    target_behavior_kl: float = 0.01
    hard_behavior_kl_limit: float = 0.05
    hard_anchor_kl_limit: float = 0.10
    early_stop_kl_multiplier: float = 2.0
    entropy_coef: float = 0.0
    value_coef: float = 1e-3
    max_grad_norm: float = 1.0
    update_epochs: int = 2
    micro_batch_size: int = 1
    min_independent_problems: int = 8
    old_logprob_tolerance: float = 5e-4
    pad_token_id: int = 0
    sampling_temperature: float = 1.5
    sampling_top_p: float = 0.9
    # Rollouts and the frozen behavior view are evaluated with adapter dropout
    # disabled.  Keeping the policy LM in eval mode preserves that probability
    # measure while still allowing autograd through trainable LoRA weights.
    policy_eval_mode: bool = True

    def validate(self) -> None:
        if not 0.0 < self.clip_epsilon < 1.0:
            raise ValueError("clip_epsilon must be in (0,1)")
        if min(self.behavior_kl_beta, self.anchor_kl_beta, self.entropy_coef, self.value_coef) < 0:
            raise ValueError("regularizer coefficients must be non-negative")
        if self.target_behavior_kl <= 0:
            raise ValueError("target_behavior_kl must be positive")
        if self.hard_behavior_kl_limit <= self.target_behavior_kl:
            raise ValueError("hard_behavior_kl_limit must exceed target_behavior_kl")
        if self.hard_anchor_kl_limit <= 0 or self.early_stop_kl_multiplier <= 1:
            raise ValueError("KL limits/multiplier are invalid")
        if self.max_grad_norm <= 0 or not 1 <= self.update_epochs <= 2:
            raise ValueError("gradient norm must be positive and update_epochs must be in [1,2]")
        if self.micro_batch_size < 1:
            raise ValueError("micro_batch_size must be positive")
        if self.min_independent_problems < 1 or self.old_logprob_tolerance <= 0:
            raise ValueError("problem count and old-logprob tolerance must be positive")
        if (self.sampling_temperature, self.sampling_top_p) != (1.5, 0.9):
            raise ValueError("formal Online-v2 requires sampling temperature=1.5 and top_p=0.9")
        if self.policy_eval_mode is not True:
            raise ValueError("formal Online-v2 requires policy_eval_mode=true")


@dataclass(frozen=True)
class UpdateReceipt:
    accepted: bool
    reason: str
    wave_id: str
    wave_digest: str
    policy_version: str
    samples: int
    independent_problems: int
    micro_batches_per_epoch: int
    optimizer_steps_total: int
    optimizer_steps_this_update: int
    epochs_completed: int
    ppo_loss: float
    behavior_exact_kl: float
    anchor_exact_kl: float
    ppo_approx_kl: float
    clip_fraction: float
    entropy: float
    value_loss: float
    value_samples: int
    grad_norm: float
    max_old_logprob_error: float
    behavior_kl_beta_before: float
    behavior_kl_beta_after: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _Snapshot:
    trainable_parameters: dict[str, torch.Tensor]
    value_parameters: dict[str, torch.Tensor]
    optimizer: dict
    scheduler: dict | None
    python_rng: object
    torch_rng: torch.Tensor
    cuda_rng: list[torch.Tensor] | None
    numpy_rng: object | None
    optimizer_steps: int
    behavior_kl_beta: float


def _extract_logits(output: object) -> torch.Tensor:
    if isinstance(output, torch.Tensor):
        return output
    logits = getattr(output, "logits", None)
    if isinstance(logits, torch.Tensor):
        return logits
    if isinstance(output, dict) and isinstance(output.get("logits"), torch.Tensor):
        return output["logits"]
    raise TypeError("model output must be a tensor or expose .logits")


def _extract_hidden(output: object) -> torch.Tensor:
    hidden = getattr(output, "hidden_states", None)
    if hidden is None and isinstance(output, dict):
        hidden = output.get("hidden_states")
    if not hidden:
        raise TypeError("value-head update requires model hidden_states")
    return hidden[-1]


class LogitsReference(Protocol):
    def __call__(self, *, input_ids: torch.Tensor, attention_mask: torch.Tensor, use_cache: bool = False) -> object: ...


def _freeze_module(module: nn.Module) -> None:
    module.eval()
    for parameter in module.parameters():
        parameter.requires_grad_(False)


class OnlineV2Learner:
    """One-shot, version-bound sequence PPO with streamed references."""

    def __init__(
        self,
        policy_model: nn.Module,
        behavior_model: LogitsReference,
        anchor_model: LogitsReference,
        optimizer: torch.optim.Optimizer,
        *,
        behavior_policy_version: str,
        behavior_wave_id: str,
        ledger_path: str | Path,
        value_head: nn.Module | None = None,
        config: OnlineV2Config | None = None,
        device: str | torch.device = "cpu",
        canary: Callable[[nn.Module], bool] | None = None,
        scheduler: object | None = None,
        adapter_references: SingleBackboneAdapterReferences | None = None,
    ) -> None:
        if not behavior_policy_version or not behavior_wave_id:
            raise ValueError("behavior policy version and wave id are required")
        self.policy_model = policy_model
        self.behavior_model = behavior_model
        self.anchor_model = anchor_model
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.behavior_policy_version = behavior_policy_version
        self.behavior_wave_id = behavior_wave_id
        self.value_head = value_head
        self.config = config or OnlineV2Config()
        self.config.validate()
        if self.config.value_coef > 0 and self.value_head is None:
            raise ValueError("positive value_coef requires a value_head")
        self.device = torch.device(device)
        self.canary = canary
        self.adapter_references = adapter_references
        self.behavior_kl_beta = self.config.behavior_kl_beta
        self.optimizer_steps = 0
        self.ledger = WaveTransactionStore(ledger_path)
        self._sealed = False
        self._active_transaction: TransactionHandle | None = None
        self._prepare_reference(self.behavior_model)
        self._prepare_reference(self.anchor_model)
        self._validate_adapter_bundle()
        self._validate_optimizer_membership()
        with self.ledger.locked():
            self._sealed = self.ledger.reconcile(self.behavior_wave_id, self._restore) == "COMMITTED"

    @property
    def sealed(self) -> bool:
        return self._sealed

    def export_training_state(self) -> dict[str, object]:
        """Serialize optimizer/scheduler/RNG continuity for the next wave.

        Policy and value parameters are published as independently hashed
        checkpoint artifacts.  This state deliberately contains only the
        mutable learner state that those artifacts cannot represent.
        """
        if self._active_transaction is not None:
            raise RuntimeError("cannot export training state during an active transaction")
        snapshot = self._snapshot()
        return {
            "schema_version": TRAINING_STATE_SCHEMA,
            "optimizer": snapshot.optimizer,
            "optimizer_parameter_names": self._optimizer_parameter_names(),
            "scheduler": snapshot.scheduler,
            "python_rng": snapshot.python_rng,
            "torch_rng": snapshot.torch_rng,
            "cuda_rng": snapshot.cuda_rng,
            "numpy_rng": snapshot.numpy_rng,
            "optimizer_steps": snapshot.optimizer_steps,
            "behavior_kl_beta": snapshot.behavior_kl_beta,
        }

    def restore_training_state(self, state: Mapping[str, object]) -> None:
        """Restore a hash-verified predecessor wave's mutable learner state."""
        if self._sealed or self._active_transaction is not None:
            raise RuntimeError("training state can only be restored before an unsealed wave")
        required = {
            "schema_version", "optimizer", "optimizer_parameter_names", "scheduler",
            "python_rng", "torch_rng", "cuda_rng", "numpy_rng", "optimizer_steps",
            "behavior_kl_beta",
        }
        if set(state) != required or state.get("schema_version") != TRAINING_STATE_SCHEMA:
            raise ValueError("unsupported or malformed cross-wave training state")
        if state["optimizer_parameter_names"] != self._optimizer_parameter_names():
            raise ValueError("optimizer parameter ordering differs from predecessor checkpoint")
        steps = state["optimizer_steps"]
        beta = state["behavior_kl_beta"]
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
            raise ValueError("cross-wave optimizer_steps must be a non-negative integer")
        if isinstance(beta, bool) or not isinstance(beta, (int, float)) or not math.isfinite(
            float(beta)
        ) or float(beta) <= 0:
            raise ValueError("cross-wave behavior_kl_beta must be finite and positive")
        scheduler_state = state["scheduler"]
        if (self.scheduler is None) != (scheduler_state is None):
            raise ValueError("scheduler presence differs from predecessor checkpoint")
        cuda_rng = state["cuda_rng"]
        if cuda_rng is not None:
            if not torch.cuda.is_available():
                raise ValueError("predecessor checkpoint requires CUDA RNG restoration")
            if not isinstance(cuda_rng, list) or len(cuda_rng) != torch.cuda.device_count():
                raise ValueError("CUDA RNG device count differs from predecessor checkpoint")

        self.optimizer.load_state_dict(state["optimizer"])  # type: ignore[arg-type]
        if self.scheduler is not None:
            self.scheduler.load_state_dict(scheduler_state)  # type: ignore[arg-type]
        random.setstate(state["python_rng"])  # type: ignore[arg-type]
        torch.random.set_rng_state(state["torch_rng"])  # type: ignore[arg-type]
        if cuda_rng is not None:
            torch.cuda.set_rng_state_all(cuda_rng)
        if state["numpy_rng"] is not None:
            import numpy as np
            np.random.set_state(state["numpy_rng"])  # type: ignore[arg-type]
        self.optimizer_steps = steps
        self.behavior_kl_beta = float(beta)
        self.optimizer.zero_grad(set_to_none=True)

    def install_behavior_wave(
        self,
        behavior_model: LogitsReference,
        *,
        policy_version: str,
        wave_id: str,
        adapter_references: SingleBackboneAdapterReferences | None = None,
    ) -> None:
        if not self._sealed:
            raise RuntimeError("cannot replace an unconsumed behavior wave")
        if not policy_version or not wave_id:
            raise ValueError("new policy version and wave id are required")
        if wave_id == self.behavior_wave_id:
            raise RuntimeError(f"wave already installed: {wave_id}")
        if self.adapter_references is not None and adapter_references is None:
            raise ValueError("a new single-backbone wave requires a new pinned adapter bundle")
        if adapter_references is not None:
            if behavior_model is not adapter_references.behavior_reference:
                raise ValueError("behavior_model must be the new adapter bundle behavior view")
            self.policy_model = adapter_references.policy_model
            self.anchor_model = adapter_references.base_reference
        self._prepare_reference(behavior_model)
        self.behavior_model = behavior_model
        self.adapter_references = adapter_references
        self.behavior_policy_version = policy_version
        self.behavior_wave_id = wave_id
        self._validate_adapter_bundle()
        self._validate_optimizer_membership()
        with self.ledger.locked():
            status = self.ledger.reconcile(wave_id, self._restore)
            if status == "COMMITTED":
                raise RuntimeError(f"wave already committed: {wave_id}")
            self._sealed = False

    def update(self, samples: ValidatedRolloutWave) -> UpdateReceipt:
        if not isinstance(samples, ValidatedRolloutWave):
            raise TypeError("learner accepts only receipt-built ValidatedRolloutWave")
        with self.ledger.locked():
            status = self.ledger.reconcile(self.behavior_wave_id, self._restore)
            if status == "COMMITTED":
                self._sealed = True
                raise RuntimeError(f"wave already committed: {self.behavior_wave_id}")
            self._sealed = False
            if self.adapter_references is None:
                return self._update_unlocked(samples)
            with self.adapter_references.exclusive_update():
                self.adapter_references.validate_runtime()
                return self._update_unlocked(samples)

    def _update_unlocked(self, samples: ValidatedRolloutWave) -> UpdateReceipt:
        if self._sealed:
            raise RuntimeError(f"wave already committed: {self.behavior_wave_id}")
        wave = list(samples.validate())
        if not wave:
            raise ValueError("cannot update from an empty wave")
        if self.adapter_references is not None:
            # The receipt identity is checked at the learner boundary, before
            # any forward/backward or optimizer mutation can occur.
            self.adapter_references.validate_wave_identity(**samples.reference_identity)
        event_ids: set[str] = set()
        for sample in wave:
            sample.validate()
            if sample.policy_version != self.behavior_policy_version:
                raise ValueError("wave contains a stale or mixed policy version")
            if sample.wave_id != self.behavior_wave_id:
                raise ValueError("wave contains a stale or mixed wave id")
            if sample.event_id in event_ids:
                raise ValueError(f"duplicate event id in wave: {sample.event_id}")
            event_ids.add(sample.event_id)
        if not any(abs(sample.advantage) > 1e-12 for sample in wave):
            raise ValueError(
                "rollout wave has no nonzero policy advantage; refusing a value/KL-only "
                "update presented as Online-v2 policy learning"
            )
        independent = len({sample.problem_id for sample in wave})
        if independent < self.config.min_independent_problems:
            raise ValueError(f"requires {self.config.min_independent_problems} independent problems, got {independent}")

        digest = samples.content_sha256
        snapshot = self._snapshot()
        self._active_transaction = self.ledger.begin(
            self.behavior_wave_id,
            self.behavior_policy_version,
            digest,
            snapshot,
        )
        beta_before = self.behavior_kl_beta
        steps_before = self.optimizer_steps
        metrics = self._empty_metrics()
        metrics["value_samples"] = sum(sample.value_distance is not None for sample in wave)
        epochs_completed = 0
        sample_weights = problem_balanced_weights(
            [sample.problem_id for sample in wave],
            [sample.sample_multiplicity for sample in wave],
        )
        value_weights = self._value_weights(wave)
        micro_batches = self._micro_batches(wave, sample_weights, value_weights)
        # ``eval`` does not disable gradients.  It only makes q_new comparable
        # to the eval-mode q_old recorded by the behavior actor (notably with
        # the frozen LoRA dropout=0.02).
        self.policy_model.eval()
        if self.value_head is not None:
            self.value_head.train()
        try:
            metrics["max_old_logprob_error"] = self._verify_old_logprobs(micro_batches)
            if not math.isfinite(metrics["max_old_logprob_error"]) or metrics["max_old_logprob_error"] > self.config.old_logprob_tolerance:
                return self._rollback_receipt(snapshot, reason="old_logprob_behavior_mismatch", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)

            for _ in range(self.config.update_epochs):
                self.optimizer.zero_grad(set_to_none=True)
                epoch_metrics = self._empty_metrics()
                epoch_metrics["value_samples"] = metrics["value_samples"]
                for batch in micro_batches:
                    local, objective = self._training_microbatch(batch)
                    objective.backward()
                    self._accumulate_metrics(epoch_metrics, local)
                epoch_metrics["max_old_logprob_error"] = metrics["max_old_logprob_error"]
                metrics = epoch_metrics
                if metrics["behavior_exact_kl"] > self.config.hard_behavior_kl_limit:
                    return self._rollback_receipt(snapshot, reason="pre_step_behavior_kl_limit", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
                if metrics["anchor_exact_kl"] > self.config.hard_anchor_kl_limit:
                    return self._rollback_receipt(snapshot, reason="pre_step_anchor_kl_limit", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
                parameters = self._trainable_parameters()
                grad_norm = torch.nn.utils.clip_grad_norm_(parameters, self.config.max_grad_norm)
                if not bool(torch.isfinite(torch.as_tensor(grad_norm)).item()):
                    raise FloatingPointError("non-finite gradient norm")
                self.optimizer.step()
                if self.scheduler is not None:
                    self.scheduler.step()
                self.optimizer_steps += 1
                epochs_completed += 1
                metrics["grad_norm"] = float(torch.as_tensor(grad_norm).detach().cpu())
                if not all(torch.isfinite(parameter).all() for parameter in parameters):
                    raise FloatingPointError("optimizer produced non-finite parameters")
                post = self._post_metrics(micro_batches)
                post["grad_norm"] = metrics["grad_norm"]
                post["max_old_logprob_error"] = metrics["max_old_logprob_error"]
                post["value_loss"] = metrics["value_loss"]
                post["value_samples"] = metrics["value_samples"]
                metrics = post
                if metrics["behavior_exact_kl"] > self.config.hard_behavior_kl_limit:
                    return self._rollback_receipt(snapshot, reason="post_step_behavior_kl_limit", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
                if metrics["anchor_exact_kl"] > self.config.hard_anchor_kl_limit:
                    return self._rollback_receipt(snapshot, reason="post_step_anchor_kl_limit", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
                if metrics["behavior_exact_kl"] > self.config.early_stop_kl_multiplier * self.config.target_behavior_kl:
                    break

            if self.canary is not None and not bool(self.canary(self.policy_model)):
                return self._rollback_receipt(snapshot, reason="canary_rejected", digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
            self._adapt_beta(metrics["behavior_exact_kl"])
            reason = "accepted_early_stop_kl" if epochs_completed < self.config.update_epochs else "accepted"
            receipt = self._receipt(accepted=True, reason=reason, digest=digest, samples=len(wave), independent=independent, micro_batches=len(micro_batches), steps_before=steps_before, epochs_completed=epochs_completed, beta_before=beta_before, metrics=metrics)
            self.ledger.commit(
                self._active_transaction,
                self._snapshot(),
                receipt.to_dict(),
            )
            self._active_transaction = None
            self._sealed = True
            self._set_eval()
            return receipt
        except Exception:
            transaction = self._active_transaction
            if transaction is not None and self.ledger.committed(transaction):
                self.ledger.restore_committed(transaction, self._restore)
                self._active_transaction = None
                self._sealed = True
                self._set_eval()
                if "receipt" in locals():
                    return receipt
                raise
            if transaction is not None:
                self.ledger.abort(transaction, "learner_exception")
                self._active_transaction = None
            self._restore(snapshot)
            self._set_eval()
            raise

    def _prepare_reference(self, reference: LogitsReference) -> None:
        if reference is self.policy_model:
            raise ValueError("reference cannot be the trainable module object")
        if isinstance(reference, nn.Module):
            _freeze_module(reference)

    def _validate_adapter_bundle(self) -> None:
        bundle = self.adapter_references
        if bundle is None:
            return
        if self.policy_model is not bundle.policy_model:
            raise ValueError("policy_model is not the pinned single-backbone policy facade")
        if self.behavior_model is not bundle.behavior_reference:
            raise ValueError("behavior_model is not the pinned single-backbone behavior view")
        if self.anchor_model is not bundle.base_reference:
            raise ValueError("anchor_model is not the pinned single-backbone base view")
        if bundle.manifest.policy_version != self.behavior_policy_version:
            raise ValueError("adapter manifest policy version does not match behavior wave")
        bundle.validate_runtime()

    def _validate_optimizer_membership(self) -> None:
        optimizer_ids = {id(parameter) for group in self.optimizer.param_groups for parameter in group["params"]}
        if any(id(parameter) not in optimizer_ids for parameter in self._trainable_parameters()):
            raise ValueError("optimizer is missing trainable policy/value-head parameters")

    def _optimizer_parameter_names(self) -> list[list[str]]:
        names = {
            id(parameter): f"policy:{name}"
            for name, parameter in self.policy_model.named_parameters()
            if parameter.requires_grad
        }
        if self.value_head is not None:
            names.update({
                id(parameter): f"value:{name}"
                for name, parameter in self.value_head.named_parameters()
                if parameter.requires_grad
            })
        groups: list[list[str]] = []
        for group in self.optimizer.param_groups:
            group_names = []
            for parameter in group["params"]:
                try:
                    group_names.append(names[id(parameter)])
                except KeyError as exc:
                    raise ValueError(
                        "optimizer contains a parameter outside trainable policy/value state"
                    ) from exc
            groups.append(group_names)
        return groups

    def _trainable_parameters(self) -> list[nn.Parameter]:
        parameters = [p for p in self.policy_model.parameters() if p.requires_grad]
        if self.value_head is not None:
            parameters.extend(p for p in self.value_head.parameters() if p.requires_grad)
        return parameters

    @staticmethod
    def _value_weights(samples: list[RolloutSample]) -> torch.Tensor:
        labeled = [sample for sample in samples if sample.value_distance is not None]
        weights = torch.zeros(len(samples), dtype=torch.float32)
        if not labeled:
            return weights
        labeled_weights = problem_balanced_weights(
            [sample.problem_id for sample in labeled],
            [sample.sample_multiplicity for sample in labeled],
        )
        cursor = 0
        for index, sample in enumerate(samples):
            if sample.value_distance is not None:
                weights[index] = labeled_weights[cursor]
                cursor += 1
        return weights

    def _micro_batches(self, samples: list[RolloutSample], sample_weights: torch.Tensor, value_weights: torch.Tensor) -> list[dict[str, torch.Tensor]]:
        size = self.config.micro_batch_size
        return [self._collate(samples[start:start + size], sample_weights[start:start + size], value_weights[start:start + size]) for start in range(0, len(samples), size)]

    def _collate(self, samples: list[RolloutSample], sample_weights: torch.Tensor, value_weights: torch.Tensor) -> dict[str, torch.Tensor]:
        length = max(sample.input_ids.numel() for sample in samples)
        batch_size = len(samples)
        input_ids = torch.full((batch_size, length), self.config.pad_token_id, dtype=torch.long, device=self.device)
        attention_mask = torch.zeros((batch_size, length), dtype=torch.long, device=self.device)
        action_mask = torch.zeros((batch_size, length), dtype=torch.bool, device=self.device)
        old_logprobs = torch.zeros((batch_size, length), dtype=torch.float32, device=self.device)
        advantages = torch.empty((batch_size,), dtype=torch.float32, device=self.device)
        value_mask = torch.zeros((batch_size,), dtype=torch.bool, device=self.device)
        value_distances = torch.zeros((batch_size,), dtype=torch.float32, device=self.device)
        value_positions = torch.zeros((batch_size,), dtype=torch.long, device=self.device)
        for row, sample in enumerate(samples):
            length_i = sample.input_ids.numel()
            input_ids[row, :length_i] = sample.input_ids.to(self.device, dtype=torch.long)
            attention_mask[row, :length_i] = sample.attention_mask.to(self.device, dtype=torch.long)
            action_mask[row, :length_i] = sample.action_mask.to(self.device, dtype=torch.bool)
            old_logprobs[row, :length_i] = sample.old_logprobs.to(self.device, dtype=torch.float32)
            advantages[row] = sample.advantage
            first_action = int(torch.nonzero(sample.action_mask, as_tuple=False)[0].item())
            value_positions[row] = first_action - 1
            if sample.value_distance is not None:
                value_mask[row] = True
                value_distances[row] = float(sample.value_distance)
        return {"input_ids": input_ids, "attention_mask": attention_mask, "action_mask": action_mask, "old_logprobs": old_logprobs, "advantages": advantages, "sample_weights": sample_weights.to(self.device), "value_weights": value_weights.to(self.device), "value_mask": value_mask, "value_distances": value_distances, "value_positions": value_positions}

    @torch.no_grad()
    def _verify_old_logprobs(self, micro_batches: list[dict[str, torch.Tensor]]) -> float:
        maximum = 0.0
        for batch in micro_batches:
            behavior_selected = self._reference_selected(self.behavior_model, batch)
            actual = selected_warped_token_logprobs(
                behavior_selected, batch["input_ids"], batch["action_mask"],
                temperature=self.config.sampling_temperature,
                top_p=self.config.sampling_top_p,
            )
            recorded = batch["old_logprobs"][:, 1:][batch["action_mask"][:, 1:]]
            maximum = max(maximum, float((actual - recorded).abs().max().cpu()))
            del behavior_selected, actual, recorded
        return maximum

    def _training_microbatch(self, batch: dict[str, torch.Tensor]) -> tuple[dict[str, float], torch.Tensor]:
        sample_mass = batch["sample_weights"].sum()
        if not bool(sample_mass > 0):
            raise ValueError("micro-batch has zero policy mass")
        policy_output = self.policy_model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"], use_cache=False, **({"output_hidden_states": True} if self.value_head is not None else {}))
        policy_selected = selected_action_logits(_extract_logits(policy_output), batch["action_mask"])
        new_selected = selected_warped_token_logprobs(
            policy_selected, batch["input_ids"], batch["action_mask"],
            temperature=self.config.sampling_temperature,
            top_p=self.config.sampling_top_p,
        )
        new_logprobs = torch.zeros_like(batch["old_logprobs"][:, 1:])
        new_logprobs[batch["action_mask"][:, 1:]] = new_selected
        ppo = sequence_clipped_ppo_loss(new_logprobs, batch["old_logprobs"][:, 1:], batch["action_mask"][:, 1:], batch["advantages"], batch["sample_weights"], self.config.clip_epsilon)
        behavior_selected = self._reference_selected(self.behavior_model, batch)
        behavior_kl = selected_exact_forward_kl(policy_selected, behavior_selected, batch["action_mask"], batch["sample_weights"])
        del behavior_selected
        anchor_selected = self._reference_selected(self.anchor_model, batch)
        anchor_kl = selected_exact_forward_kl(policy_selected, anchor_selected, batch["action_mask"], batch["sample_weights"])
        del anchor_selected
        entropy = selected_masked_entropy(policy_selected, batch["action_mask"], batch["sample_weights"])
        value_loss = torch.zeros((), device=self.device)
        value_mass = batch["value_weights"].sum()
        if self.value_head is not None and bool(value_mass > 0):
            hidden = _extract_hidden(policy_output)
            rows = torch.arange(hidden.shape[0], device=self.device)
            state_hidden = hidden[rows, batch["value_positions"]]
            value_mask = batch["value_mask"]
            value_logits = self.value_head(state_hidden[value_mask].float())
            value_loss = weighted_categorical_value_loss(value_logits, batch["value_distances"][value_mask], batch["value_weights"][value_mask])
        objective = sample_mass * (ppo.loss + self.behavior_kl_beta * behavior_kl + self.config.anchor_kl_beta * anchor_kl - self.config.entropy_coef * entropy)
        if self.value_head is not None and bool(value_mass > 0):
            objective = objective + self.config.value_coef * value_mass * value_loss
        if not bool(torch.isfinite(objective).item()):
            raise FloatingPointError("non-finite online objective")
        local = {"ppo_loss": float((sample_mass * ppo.loss).detach().cpu()), "behavior_exact_kl": float((sample_mass * behavior_kl).detach().cpu()), "anchor_exact_kl": float((sample_mass * anchor_kl).detach().cpu()), "ppo_approx_kl": float((sample_mass * ppo.approx_kl).detach().cpu()), "clip_fraction": float((sample_mass * ppo.clip_fraction).detach().cpu()), "entropy": float((sample_mass * entropy).detach().cpu()), "value_loss": float((value_mass * value_loss).detach().cpu())}
        return local, objective

    @torch.no_grad()
    def _post_metrics(self, micro_batches: list[dict[str, torch.Tensor]]) -> dict[str, float]:
        metrics = self._empty_metrics()
        for batch in micro_batches:
            sample_mass = batch["sample_weights"].sum()
            policy_output = self.policy_model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"], use_cache=False)
            policy_selected = selected_action_logits(_extract_logits(policy_output), batch["action_mask"])
            selected_logprobs = selected_warped_token_logprobs(
                policy_selected, batch["input_ids"], batch["action_mask"],
                temperature=self.config.sampling_temperature,
                top_p=self.config.sampling_top_p,
            )
            new_logprobs = torch.zeros_like(batch["old_logprobs"][:, 1:])
            new_logprobs[batch["action_mask"][:, 1:]] = selected_logprobs
            ppo = sequence_clipped_ppo_loss(new_logprobs, batch["old_logprobs"][:, 1:], batch["action_mask"][:, 1:], batch["advantages"], batch["sample_weights"], self.config.clip_epsilon)
            behavior_selected = self._reference_selected(self.behavior_model, batch)
            behavior_kl = selected_exact_forward_kl(policy_selected, behavior_selected, batch["action_mask"], batch["sample_weights"])
            del behavior_selected
            anchor_selected = self._reference_selected(self.anchor_model, batch)
            anchor_kl = selected_exact_forward_kl(policy_selected, anchor_selected, batch["action_mask"], batch["sample_weights"])
            del anchor_selected
            entropy = selected_masked_entropy(policy_selected, batch["action_mask"], batch["sample_weights"])
            self._accumulate_metrics(metrics, {"ppo_loss": float((sample_mass * ppo.loss).cpu()), "behavior_exact_kl": float((sample_mass * behavior_kl).cpu()), "anchor_exact_kl": float((sample_mass * anchor_kl).cpu()), "ppo_approx_kl": float((sample_mass * ppo.approx_kl).cpu()), "clip_fraction": float((sample_mass * ppo.clip_fraction).cpu()), "entropy": float((sample_mass * entropy).cpu()), "value_loss": 0.0})
        return metrics

    @torch.no_grad()
    def _reference_selected(self, model: LogitsReference, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        output = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"], use_cache=False)
        selected = selected_action_logits(_extract_logits(output), batch["action_mask"]).detach()
        del output
        return selected

    @staticmethod
    def _accumulate_metrics(target: dict[str, float], source: dict[str, float]) -> None:
        for key, value in source.items():
            target[key] += value

    def _adapt_beta(self, observed_kl: float) -> None:
        if observed_kl > 1.5 * self.config.target_behavior_kl:
            self.behavior_kl_beta = min(100.0, max(1e-8, self.behavior_kl_beta * 2.0))
        elif observed_kl < self.config.target_behavior_kl / 1.5:
            self.behavior_kl_beta = max(1e-8, self.behavior_kl_beta / 2.0)

    def _snapshot(self) -> _Snapshot:
        try:
            import numpy as np
            numpy_rng = copy.deepcopy(np.random.get_state())
        except ImportError:
            numpy_rng = None
        return _Snapshot(trainable_parameters={name: parameter.detach().clone() for name, parameter in self.policy_model.named_parameters() if parameter.requires_grad}, value_parameters={name: parameter.detach().clone() for name, parameter in (self.value_head.named_parameters() if self.value_head is not None else []) if parameter.requires_grad}, optimizer=copy.deepcopy(self.optimizer.state_dict()), scheduler=(copy.deepcopy(self.scheduler.state_dict()) if self.scheduler is not None else None), python_rng=random.getstate(), torch_rng=torch.random.get_rng_state().clone(), cuda_rng=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None, numpy_rng=numpy_rng, optimizer_steps=self.optimizer_steps, behavior_kl_beta=self.behavior_kl_beta)

    def _restore(self, snapshot: _Snapshot) -> None:
        current = dict(self.policy_model.named_parameters())
        if not current.keys() >= snapshot.trainable_parameters.keys():
            raise RuntimeError("trainable parameter set changed during update")
        with torch.no_grad():
            for name, value in snapshot.trainable_parameters.items():
                current[name].copy_(value)
        if self.value_head is not None:
            value_current = dict(self.value_head.named_parameters())
            if not value_current.keys() >= snapshot.value_parameters.keys():
                raise RuntimeError("value-head trainable parameter set changed during update")
            with torch.no_grad():
                for name, value in snapshot.value_parameters.items():
                    value_current[name].copy_(value)
        self.optimizer.load_state_dict(snapshot.optimizer)
        if self.scheduler is not None and snapshot.scheduler is not None:
            self.scheduler.load_state_dict(snapshot.scheduler)
        random.setstate(snapshot.python_rng)
        torch.random.set_rng_state(snapshot.torch_rng)
        if snapshot.cuda_rng is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(snapshot.cuda_rng)
        if snapshot.numpy_rng is not None:
            import numpy as np
            np.random.set_state(snapshot.numpy_rng)
        self.optimizer_steps = snapshot.optimizer_steps
        self.behavior_kl_beta = snapshot.behavior_kl_beta
        self.optimizer.zero_grad(set_to_none=True)

    @staticmethod
    def _empty_metrics() -> dict[str, float]:
        return {"ppo_loss": 0.0, "behavior_exact_kl": 0.0, "anchor_exact_kl": 0.0, "ppo_approx_kl": 0.0, "clip_fraction": 0.0, "entropy": 0.0, "value_loss": 0.0, "value_samples": 0, "grad_norm": 0.0, "max_old_logprob_error": 0.0}

    def _receipt(self, *, accepted: bool, reason: str, digest: str, samples: int, independent: int, micro_batches: int, steps_before: int, epochs_completed: int, beta_before: float, metrics: dict[str, float]) -> UpdateReceipt:
        return UpdateReceipt(accepted=accepted, reason=reason, wave_id=self.behavior_wave_id, wave_digest=digest, policy_version=self.behavior_policy_version, samples=samples, independent_problems=independent, micro_batches_per_epoch=micro_batches, optimizer_steps_total=self.optimizer_steps, optimizer_steps_this_update=self.optimizer_steps - steps_before, epochs_completed=epochs_completed, behavior_kl_beta_before=beta_before, behavior_kl_beta_after=self.behavior_kl_beta, **metrics)

    def _rollback_receipt(self, snapshot: _Snapshot, **kwargs) -> UpdateReceipt:
        if self._active_transaction is not None:
            self.ledger.abort(
                self._active_transaction,
                str(kwargs.get("reason", "learner_rejected")),
            )
            self._active_transaction = None
        self._restore(snapshot)
        self._set_eval()
        return self._receipt(accepted=False, **kwargs)

    def _set_eval(self) -> None:
        self.policy_model.eval()
        if self.value_head is not None:
            self.value_head.eval()

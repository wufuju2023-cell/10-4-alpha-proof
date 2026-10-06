from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
from typing import Callable, ContextManager, Iterator, Mapping, Protocol

import torch
from torch import nn


class ReferenceIdentityError(RuntimeError):
    """A pinned base or behavior identity no longer matches the live backend."""


@dataclass(frozen=True)
class ArtifactIdentity:
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("identity version must be non-empty")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("identity sha256 must be a 64-character hexadecimal digest")
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True)
class AdapterReferenceManifest:
    """Frozen identities for one rollout wave.

    The policy adapter may change during the update, so only its logical version
    is pinned.  The behavior adapter and base model must remain byte-identical.
    """

    policy_adapter: str
    policy_version: str
    behavior_adapter: str
    behavior: ArtifactIdentity
    base: ArtifactIdentity

    def __post_init__(self) -> None:
        if not self.policy_adapter or not self.behavior_adapter:
            raise ValueError("policy and behavior adapter names must be non-empty")
        if self.policy_adapter == self.behavior_adapter:
            raise ValueError("policy and behavior must be different adapters")
        if not self.policy_version:
            raise ValueError("policy_version must be non-empty")
        if self.policy_version != self.behavior.version:
            raise ValueError("behavior version must equal the rollout policy version")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_adapter": self.policy_adapter,
            "policy_version": self.policy_version,
            "behavior_adapter": self.behavior_adapter,
            "behavior": {
                "version": self.behavior.version,
                "sha256": self.behavior.sha256,
            },
            "base": {"version": self.base.version, "sha256": self.base.sha256},
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class SingleBackboneBackend(Protocol):
    """Minimal backend contract; deliberately small enough for a fake backend."""

    @property
    def model(self) -> nn.Module: ...

    def activate_adapter(self, name: str) -> None: ...

    def adapters_disabled(self) -> ContextManager[None]: ...

    def adapter_identity(self, name: str) -> ArtifactIdentity: ...

    def base_identity(self) -> ArtifactIdentity: ...


def sha256_state_dict(state: Mapping[str, object]) -> str:
    """Hash tensor state deterministically without torch serialization metadata."""

    digest = hashlib.sha256()
    for key in sorted(state):
        value = state[key]
        encoded_key = key.encode("utf-8")
        digest.update(len(encoded_key).to_bytes(8, "big"))
        digest.update(encoded_key)
        if isinstance(value, torch.Tensor):
            tensor = value.detach().cpu().contiguous()
            metadata = f"tensor:{tensor.dtype}:{tuple(tensor.shape)}".encode("ascii")
            payload = tensor.reshape(-1).view(torch.uint8).numpy().tobytes()
        else:
            metadata = f"object:{type(value).__qualname__}".encode("utf-8")
            payload = repr(value).encode("utf-8")
        digest.update(len(metadata).to_bytes(8, "big"))
        digest.update(metadata)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class PeftSingleBackboneBackend:
    """PEFT backend over one model/backbone allocation.

    ``base_identity`` comes from the pinned model receipt, while
    ``base_state_getter`` must return the live frozen-backbone state.  This
    class hashes that state itself; accepting an already-hashed identity would
    allow a caller to pass the pinned receipt back as a fake "live" check.
    """

    def __init__(
        self,
        model: nn.Module,
        *,
        base_identity: ArtifactIdentity,
        adapter_versions: Mapping[str, str],
        adapter_state_getter: Callable[[str], Mapping[str, object]] | None = None,
        base_state_getter: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        if not callable(getattr(model, "set_adapter", None)):
            raise TypeError("PEFT model must expose set_adapter(name)")
        if not callable(getattr(model, "disable_adapter", None)):
            raise TypeError("PEFT model must expose disable_adapter() context manager")
        if base_state_getter is None:
            raise TypeError(
                "base_state_getter is required and must expose the live frozen base state"
            )
        self._model = model
        self._base_identity = base_identity
        self._base_state_getter = base_state_getter
        self._adapter_versions = dict(adapter_versions)
        if not self._adapter_versions or any(not key or not value for key, value in self._adapter_versions.items()):
            raise ValueError("adapter_versions must map non-empty names to versions")
        self._state_getter = adapter_state_getter or self._default_state_getter

    @property
    def model(self) -> nn.Module:
        return self._model

    def activate_adapter(self, name: str) -> None:
        if name not in self._adapter_versions:
            raise KeyError(f"unknown adapter: {name}")
        self._model.set_adapter(name)

    def adapters_disabled(self) -> ContextManager[None]:
        return self._model.disable_adapter()

    def adapter_identity(self, name: str) -> ArtifactIdentity:
        try:
            version = self._adapter_versions[name]
        except KeyError as error:
            raise KeyError(f"unknown adapter: {name}") from error
        return ArtifactIdentity(version=version, sha256=sha256_state_dict(self._state_getter(name)))

    def base_identity(self) -> ArtifactIdentity:
        state = self._base_state_getter()
        if not isinstance(state, Mapping):
            raise TypeError("base_state_getter must return a mapping of live base tensors")
        if not state:
            raise ValueError("base_state_getter returned an empty base state")
        return ArtifactIdentity(
            version=self._base_identity.version,
            sha256=sha256_state_dict(state),
        )

    def _default_state_getter(self, name: str) -> Mapping[str, object]:
        getter = getattr(self._model, "get_adapter_state_dict", None)
        if not callable(getter):
            raise TypeError(
                "this PEFT version does not expose get_adapter_state_dict; "
                "pass adapter_state_getter explicitly"
            )
        try:
            state = getter(adapter_name=name)
        except TypeError:
            # Some PEFT/Transformers integrations use a positional adapter name.
            state = getter(name)
        if not isinstance(state, Mapping):
            raise TypeError("adapter state getter must return a mapping")
        return state


class _PolicyFacade(nn.Module):
    """An unregistered facade, so references never become child modules."""

    def __init__(self, owner: "SingleBackboneAdapterReferences") -> None:
        super().__init__()
        object.__setattr__(self, "_owner", owner)

    def forward(self, *args: object, **kwargs: object) -> object:
        return self._owner._policy_forward(*args, **kwargs)

    def train(self, mode: bool = True) -> "_PolicyFacade":
        with self._owner.locked():
            self._owner._validate_base()
            self._owner._restore_policy()
            self._owner.backend.model.train(mode)
        self.training = mode
        return self

    def parameters(self, recurse: bool = True):  # type: ignore[override]
        return self._owner.backend.model.parameters(recurse=recurse)

    def named_parameters(
        self,
        prefix: str = "",
        recurse: bool = True,
        remove_duplicate: bool = True,
    ):
        return self._owner.backend.model.named_parameters(
            prefix=prefix, recurse=recurse, remove_duplicate=remove_duplicate
        )


class _ReferenceView:
    def __init__(self, owner: "SingleBackboneAdapterReferences", kind: str) -> None:
        self._owner = owner
        self._kind = kind

    def __call__(self, *args: object, **kwargs: object) -> object:
        return self._owner._reference_forward(self._kind, *args, **kwargs)


class SingleBackboneAdapterReferences:
    """Locked policy/behavior/base views sharing one PEFT backbone.

    Use ``exclusive_update`` around the complete learner update, not merely one
    forward.  Its re-entrant lock lets policy/reference forwards nest while
    preventing rollout threads from switching adapters during backward/step.
    """

    def __init__(
        self,
        backend: SingleBackboneBackend,
        manifest: AdapterReferenceManifest,
        *,
        lock: RLock | None = None,
    ) -> None:
        self.backend = backend
        self.manifest = manifest
        self._lock = lock or RLock()
        self.policy_model = _PolicyFacade(self)
        self.behavior_reference = _ReferenceView(self, "behavior")
        self.base_reference = _ReferenceView(self, "base")
        with self._lock:
            self.validate_runtime()
            self._restore_policy()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    @contextmanager
    def exclusive_update(self) -> Iterator[None]:
        """Hold adapter ownership across forward, backward, optimizer and rollback."""

        with self._lock:
            self.validate_runtime()
            self._restore_policy()
            try:
                yield
            finally:
                self._restore_policy()

    def validate_wave_identity(
        self,
        *,
        policy_version: str,
        behavior_version: str,
        behavior_sha256: str,
        base_version: str,
        base_sha256: str,
    ) -> None:
        expected = self.manifest
        supplied_behavior = behavior_sha256.lower()
        supplied_base = base_sha256.lower()
        if policy_version != expected.policy_version:
            raise ReferenceIdentityError(
                f"policy version mismatch: expected {expected.policy_version}, got {policy_version}"
            )
        if behavior_version != expected.behavior.version:
            raise ReferenceIdentityError(
                "behavior version mismatch: "
                f"expected {expected.behavior.version}, got {behavior_version}"
            )
        if supplied_behavior != expected.behavior.sha256:
            raise ReferenceIdentityError("wave behavior hash does not match pinned behavior")
        if base_version != expected.base.version or supplied_base != expected.base.sha256:
            raise ReferenceIdentityError("wave base identity does not match pinned base")

    def validate_runtime(self) -> None:
        self._validate_base()
        actual = self.backend.adapter_identity(self.manifest.behavior_adapter)
        if actual != self.manifest.behavior:
            raise ReferenceIdentityError(
                "live behavior adapter identity changed: "
                f"expected {self.manifest.behavior}, got {actual}"
            )

    def _validate_base(self) -> None:
        actual = self.backend.base_identity()
        if actual != self.manifest.base:
            raise ReferenceIdentityError(
                f"live base identity changed: expected {self.manifest.base}, got {actual}"
            )

    def _restore_policy(self) -> None:
        self.backend.activate_adapter(self.manifest.policy_adapter)

    def _policy_forward(self, *args: object, **kwargs: object) -> object:
        with self._lock:
            self._validate_base()
            self._restore_policy()
            try:
                return self.backend.model(*args, **kwargs)
            finally:
                self._restore_policy()

    def _reference_forward(self, kind: str, *args: object, **kwargs: object) -> object:
        with self._lock:
            self.validate_runtime()
            was_training = self.backend.model.training
            self.backend.model.eval()
            try:
                with torch.no_grad():
                    if kind == "behavior":
                        self.backend.activate_adapter(self.manifest.behavior_adapter)
                        return self.backend.model(*args, **kwargs)
                    if kind == "base":
                        with self.backend.adapters_disabled():
                            return self.backend.model(*args, **kwargs)
                    raise ValueError(f"unknown reference kind: {kind}")
            finally:
                # Restore both mutable pieces of shared-model state even when
                # forward or a disable-adapter context raises.
                self._restore_policy()
                self.backend.model.train(was_training)

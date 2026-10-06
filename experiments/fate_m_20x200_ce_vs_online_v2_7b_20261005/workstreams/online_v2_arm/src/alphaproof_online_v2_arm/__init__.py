"""Online-v2 training primitives for the FATE-M CE comparison."""

from .adapter_reference import (
    AdapterReferenceManifest,
    ArtifactIdentity,
    PeftSingleBackboneBackend,
    SingleBackboneAdapterReferences,
)
from .buffer import ProblemGroupedBuffer, compute_search_advantages
from .builder import ValidatedRolloutWave, build_rollout_samples, validate_training_wave
from .learner import OnlineV2Config, OnlineV2Learner, UpdateReceipt
from .receipts import (
    CandidateReceipt,
    ProofPathReceipt,
    ReceiptInvariantError,
    SearchStateReceipt,
    VerifierReceipt,
    ordered_proof_chain_sha256,
)
from .rewards import VerificationRecord, strict_local_reward
from .schema import RolloutSample, SearchObservation
from .real_runner import load_join_receipts

__all__ = [
    "OnlineV2Config",
    "OnlineV2Learner",
    "ProblemGroupedBuffer",
    "ProofPathReceipt",
    "ReceiptInvariantError",
    "RolloutSample",
    "CandidateReceipt",
    "SearchStateReceipt",
    "SearchObservation",
    "UpdateReceipt",
    "VerificationRecord",
    "VerifierReceipt",
    "ordered_proof_chain_sha256",
    "ValidatedRolloutWave",
    "AdapterReferenceManifest",
    "ArtifactIdentity",
    "PeftSingleBackboneBackend",
    "SingleBackboneAdapterReferences",
    "build_rollout_samples",
    "validate_training_wave",
    "compute_search_advantages",
    "strict_local_reward",
    "load_join_receipts",
]

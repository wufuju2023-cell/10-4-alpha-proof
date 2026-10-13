"""Verified online adaptation and teacher-distillation primitives."""

from .buffer import ProblemGroupedBuffer, compute_search_advantages
from .learner import OnlineV2Config, OnlineV2Learner, UpdateReceipt
from .schema import DistillationExample, RolloutSample, SearchObservation
from .teacher import (
    OpenAICompatibleTeacher,
    TeacherConfig,
    VerificationDecision,
    collect_verified_distillation,
)

__all__ = [
    "DistillationExample",
    "OnlineV2Config",
    "OnlineV2Learner",
    "OpenAICompatibleTeacher",
    "ProblemGroupedBuffer",
    "RolloutSample",
    "SearchObservation",
    "TeacherConfig",
    "UpdateReceipt",
    "VerificationDecision",
    "collect_verified_distillation",
    "compute_search_advantages",
]

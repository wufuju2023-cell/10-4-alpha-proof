"""In-process REAL-Prover policy service with immutable rollout evidence."""

from .bridge import CandidateObservation, receipt_to_search_state
from .receipts import ReceiptError, load_committed_receipt
from .prompting import parse_reap_tactic_state
from .service import (
    IdentitySnapshot,
    InProcessPolicyService,
    PolicyHTTPServer,
    make_live_identity_provider,
    make_peft_active_session_provider,
    start_policy_server,
)

__all__ = [
    "CandidateObservation",
    "IdentitySnapshot",
    "InProcessPolicyService",
    "PolicyHTTPServer",
    "ReceiptError",
    "load_committed_receipt",
    "parse_reap_tactic_state",
    "receipt_to_search_state",
    "make_live_identity_provider",
    "make_peft_active_session_provider",
    "start_policy_server",
]

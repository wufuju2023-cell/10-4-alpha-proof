from pathlib import Path

import pytest

from alphaproof_online_v2_arm.ledger import WaveTransactionStore


def test_transaction_operations_fail_closed_without_cross_process_lock(
    tmp_path: Path,
) -> None:
    store = WaveTransactionStore(tmp_path / "ledger.json")
    with pytest.raises(RuntimeError, match="cross-process lock"):
        store.begin("wave", "policy", "digest", {"state": "before"})
    assert not store.path.exists()


def test_committed_receipt_requires_exact_resume_provenance(tmp_path: Path) -> None:
    store = WaveTransactionStore(tmp_path / "ledger.json")
    with store.locked():
        handle = store.begin("wave", "policy", "digest", {"state": "before"})
        store.commit(handle, {"state": "after"}, {"accepted": True})
        assert store.committed_receipt(
            "wave", policy_version="policy", digest="digest"
        ) == {"accepted": True}
        with pytest.raises(RuntimeError, match="provenance"):
            store.committed_receipt("wave", policy_version="policy", digest="other")

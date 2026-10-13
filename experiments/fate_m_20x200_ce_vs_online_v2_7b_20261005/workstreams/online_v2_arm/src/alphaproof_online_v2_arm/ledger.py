from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable, Iterator
import uuid

import torch


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _cross_process_lock(path: Path) -> Iterator[None]:
    """Portable advisory exclusive lock, paired with an in-process RLock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path):
        with path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_torch(path: Path, payload: object) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        torch.save(payload, temp_path)
        # Windows rejects FlushFileBuffers/fsync on a read-only descriptor.
        # Open read/write so the durable-write barrier is portable.
        with temp_path.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return {"path": str(path.resolve()), "sha256": _sha256_file(path)}


def _validate_artifact(reference: dict[str, str]) -> Path:
    path = Path(reference["path"])
    if not path.is_file() or _sha256_file(path) != reference["sha256"]:
        raise RuntimeError(f"transaction artifact missing or corrupt: {path}")
    return path


@dataclass(frozen=True)
class TransactionHandle:
    wave_id: str
    transaction_id: str
    policy_version: str
    digest: str


class WaveTransactionStore:
    """Locked PREPARED/COMMITTED wave ledger with checkpoint recovery.

    The caller holds ``locked()`` for the entire learner update. A crashed
    process releases the OS lock; the next caller restores the PREPARED
    before-checkpoint and marks it ABORTED. COMMITTED entries are valid only if
    their post-checkpoint and receipt hashes still match.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.artifact_root = self.path.with_suffix(self.path.suffix + ".artifacts")
        self._lock_state = threading.local()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with _cross_process_lock(self.lock_path):
            self._lock_state.held = True
            try:
                yield
            finally:
                self._lock_state.held = False

    def _require_locked(self) -> None:
        if not bool(getattr(self._lock_state, "held", False)):
            raise RuntimeError(
                "wave transaction operation requires the store's cross-process lock"
            )

    def reconcile(self, wave_id: str, restore: Callable[[object], None]) -> str | None:
        self._require_locked()
        data = self._load()
        entry = data["waves"].get(wave_id)
        if entry is None:
            return None
        status = entry["status"]
        if status == "PREPARED":
            before = _validate_artifact(entry["before_checkpoint"])
            restore(torch.load(before, map_location="cpu", weights_only=False))
            entry["status"] = "ABORTED"
            entry["abort_reason"] = "recovered_incomplete_transaction"
            self._write(data)
            return "ABORTED"
        if status == "COMMITTED":
            checkpoint = _validate_artifact(entry["committed_checkpoint"])
            _validate_artifact(entry["receipt"])
            restore(torch.load(checkpoint, map_location="cpu", weights_only=False))
            return "COMMITTED"
        if status == "ABORTED":
            # An exception may occur after recording ABORTED but before the
            # in-memory rollback finishes. Replaying the hashed before-image is
            # idempotent and makes that ordering crash safe as well.
            before = _validate_artifact(entry["before_checkpoint"])
            restore(torch.load(before, map_location="cpu", weights_only=False))
            return "ABORTED"
        raise RuntimeError(f"unknown transaction status: {status}")

    def begin(
        self,
        wave_id: str,
        policy_version: str,
        digest: str,
        before_checkpoint: object,
    ) -> TransactionHandle:
        self._require_locked()
        data = self._load()
        existing = data["waves"].get(wave_id)
        if existing is not None and existing["status"] == "COMMITTED":
            raise RuntimeError(f"wave already committed: {wave_id}")
        if existing is not None and existing["status"] == "PREPARED":
            raise RuntimeError(f"wave already has an active transaction: {wave_id}")
        transaction_id = uuid.uuid4().hex
        directory = self._transaction_dir(wave_id, transaction_id)
        before_ref = _atomic_torch(directory / "before.pt", before_checkpoint)
        data["waves"][wave_id] = {
            "status": "PREPARED",
            "transaction_id": transaction_id,
            "policy_version": policy_version,
            "digest": digest,
            "before_checkpoint": before_ref,
        }
        self._write(data)
        return TransactionHandle(wave_id, transaction_id, policy_version, digest)

    def commit(
        self,
        handle: TransactionHandle,
        committed_checkpoint: object,
        receipt: dict[str, object],
    ) -> None:
        self._require_locked()
        directory = self._transaction_dir(handle.wave_id, handle.transaction_id)
        checkpoint_ref = _atomic_torch(directory / "committed.pt", committed_checkpoint)
        receipt_path = directory / "receipt.json"
        _atomic_json(receipt_path, receipt)
        receipt_ref = {"path": str(receipt_path.resolve()), "sha256": _sha256_file(receipt_path)}
        data = self._load()
        entry = data["waves"].get(handle.wave_id)
        self._require_prepared(entry, handle)
        entry["committed_checkpoint"] = checkpoint_ref
        entry["receipt"] = receipt_ref
        entry["status"] = "COMMITTED"
        self._write(data)

    def abort(self, handle: TransactionHandle, reason: str) -> None:
        self._require_locked()
        data = self._load()
        entry = data["waves"].get(handle.wave_id)
        if entry is None:
            return
        if entry["status"] == "COMMITTED":
            return
        self._require_prepared(entry, handle)
        entry["status"] = "ABORTED"
        entry["abort_reason"] = reason
        self._write(data)

    def committed(self, handle: TransactionHandle) -> bool:
        self._require_locked()
        entry = self._load()["waves"].get(handle.wave_id)
        return bool(
            entry
            and entry.get("status") == "COMMITTED"
            and entry.get("transaction_id") == handle.transaction_id
            and entry.get("digest") == handle.digest
        )

    def restore_committed(self, handle: TransactionHandle, restore: Callable[[object], None]) -> None:
        self._require_locked()
        entry = self._load()["waves"].get(handle.wave_id)
        if not self.committed(handle):
            raise RuntimeError("transaction is not committed")
        checkpoint = _validate_artifact(entry["committed_checkpoint"])
        _validate_artifact(entry["receipt"])
        restore(torch.load(checkpoint, map_location="cpu", weights_only=False))

    def committed_receipt(
        self, wave_id: str, *, policy_version: str, digest: str,
    ) -> dict[str, object]:
        """Return a hash-verified committed receipt for exact resume inputs."""

        self._require_locked()
        entry = self._load()["waves"].get(wave_id)
        if not entry or entry.get("status") != "COMMITTED":
            raise RuntimeError(f"wave is not committed: {wave_id}")
        if entry.get("policy_version") != policy_version or entry.get("digest") != digest:
            raise RuntimeError("committed wave provenance differs from resume inputs")
        _validate_artifact(entry["committed_checkpoint"])
        receipt_path = _validate_artifact(entry["receipt"])
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("committed update receipt is not an object")
        return value

    def _transaction_dir(self, wave_id: str, transaction_id: str) -> Path:
        safe_wave = hashlib.sha256(wave_id.encode("utf-8")).hexdigest()[:20]
        return self.artifact_root / safe_wave / transaction_id

    @staticmethod
    def _require_prepared(entry: dict | None, handle: TransactionHandle) -> None:
        if (
            entry is None
            or entry.get("status") != "PREPARED"
            or entry.get("transaction_id") != handle.transaction_id
            or entry.get("policy_version") != handle.policy_version
            or entry.get("digest") != handle.digest
        ):
            raise RuntimeError("transaction compare-and-swap failed")

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 2, "waves": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 2 or not isinstance(data.get("waves"), dict):
            raise ValueError("invalid wave transaction ledger")
        return data

    def _write(self, data: dict) -> None:
        _atomic_json(self.path, data)

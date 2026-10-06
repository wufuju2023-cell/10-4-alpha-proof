"""Atomic, session-scoped checkpoint ACKs and post-run evidence audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CheckpointAudit:
    valid: bool
    checkpoints: int
    acknowledgements: int
    final_policy_version: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _padded(step: int) -> str:
    return f"{step:06d}"


def write_policy_state(session_dir: Path, *, session_id: str, tree_id: str, step: int, policy_version: int) -> Path:
    if step < 0 or policy_version < 0:
        raise ValueError("policy state values must be nonnegative")
    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / "current_policy_state.json"
    payload = (json.dumps({
        "schema_version": "fate.policy_state.v1", "session_id": session_id,
        "tree_id": tree_id, "step": step, "policy_version": policy_version,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".policy-state.", suffix=".tmp", dir=session_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def write_checkpoint_ack(
    checkpoint_dir: Path,
    *,
    session_id: str,
    tree_id: str,
    step: int,
    previous_policy_version: int,
    policy_version: int,
    coordinator_run_id: str,
    model_sha256: str,
) -> Path:
    if step < 0 or previous_policy_version < 0 or policy_version <= previous_policy_version:
        raise ValueError("checkpoint versions must advance monotonically")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", coordinator_run_id):
        raise ValueError("invalid coordinator_run_id")
    if not SHA256_RE.fullmatch(model_sha256):
        raise ValueError("model_sha256 is invalid")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    target = checkpoint_dir / f"checkpoint-{_padded(step)}.ack.json"
    if target.exists():
        raise FileExistsError(f"refusing to replace checkpoint ACK: {target}")
    receipt = {
        "schema_version": "reap.training.checkpoint_ack.v2",
        "session_id": session_id, "tree_id": tree_id, "step": step,
        "previous_policy_version": previous_policy_version,
        "policy_version": policy_version, "status": "continue",
        "coordinator_run_id": coordinator_run_id, "model_sha256": model_sha256,
    }
    payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=checkpoint_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # No model request can run while Lean waits at this barrier. Publish the
        # new proxy-visible state before releasing Lean with the ACK rename.
        write_policy_state(checkpoint_dir.parent, session_id=session_id, tree_id=tree_id,
                           step=step + 1, policy_version=policy_version)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    coordinator_receipt = {
        "schema_version": "fate.checkpoint.coordinator.v1",
        "session_id": session_id, "tree_id": tree_id, "step": step,
        "previous_policy_version": previous_policy_version,
        "policy_version": policy_version, "coordinator_run_id": coordinator_run_id,
        "ack_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with (checkpoint_dir / "coordinator_receipts.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(coordinator_receipt, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return target


def audit_checkpoints(
    observer_path: Path,
    checkpoint_dir: Path,
    *,
    session_id: str,
    tree_id: str,
    initial_policy_version: int,
) -> CheckpointAudit:
    if not observer_path.is_file() or not checkpoint_dir.is_dir():
        return CheckpointAudit(False, 0, 0, initial_policy_version, "missing observer/checkpoint directory")
    checkpoints: dict[int, int] = {}
    observed_acks: dict[int, tuple[int, int]] = {}
    try:
        observer_lines = observer_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return CheckpointAudit(False, 0, 0, initial_policy_version, str(exc))
    for number, line in enumerate(observer_lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), initial_policy_version, f"bad observer JSON line {number}")
        if (not isinstance(record, dict) or record.get("session_id") != session_id
                or record.get("tree_id") != tree_id):
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), initial_policy_version, f"observer session/tree mismatch line {number}")
        kind = record.get("kind")
        if kind == "checkpoint":
            step, version = record.get("step"), record.get("policy_version")
            if type(step) is not int or type(version) is not int or step in checkpoints:
                return CheckpointAudit(False, len(checkpoints), len(observed_acks), initial_policy_version, f"invalid checkpoint line {number}")
            checkpoints[step] = version
        elif kind == "checkpoint_ack":
            step = record.get("step")
            previous, new = record.get("previous_policy_version"), record.get("next_policy_version")
            if any(type(value) is not int for value in (step, previous, new)) or step in observed_acks:
                return CheckpointAudit(False, len(checkpoints), len(observed_acks), initial_policy_version, f"invalid checkpoint_ack line {number}")
            observed_acks[step] = (previous, new)
    if not checkpoints:
        return CheckpointAudit(False, 0, 0, initial_policy_version, "checkpoint mode produced no checkpoints")
    version = initial_policy_version
    expected_coordinator: dict[int, tuple[int, int, str]] = {}
    for step in sorted(checkpoints):
        if checkpoints[step] != version:
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, f"observer version drift at step {step}")
        ack_path = checkpoint_dir / f"checkpoint-{_padded(step)}.ack.json"
        try:
            ack = json.loads(ack_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, f"missing/invalid ACK at step {step}")
        if (not isinstance(ack, dict) or ack.get("schema_version") != "reap.training.checkpoint_ack.v2"
                or ack.get("session_id") != session_id or ack.get("tree_id") != tree_id
                or type(ack.get("step")) is not int or ack["step"] != step
                or type(ack.get("previous_policy_version")) is not int or ack["previous_policy_version"] != version
                or type(ack.get("policy_version")) is not int or ack["policy_version"] <= version
                or ack.get("status") != "continue" or not SHA256_RE.fullmatch(str(ack.get("model_sha256", "")))):
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, f"ACK binding failure at step {step}")
        pair = observed_acks.get(step)
        if pair != (version, ack["policy_version"]):
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, f"observer/ACK mismatch at step {step}")
        expected_coordinator[step] = (
            version, ack["policy_version"], hashlib.sha256(ack_path.read_bytes()).hexdigest()
        )
        version = ack["policy_version"]
    if set(checkpoints) != set(observed_acks):
        return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, "checkpoint/ACK coverage mismatch")
    coordinator = checkpoint_dir / "coordinator_receipts.jsonl"
    if not coordinator.is_file():
        return CheckpointAudit(False, len(checkpoints), len(observed_acks), version, "coordinator receipt coverage mismatch")
    coordinator_steps: set[int] = set()
    for number, line in enumerate(coordinator.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version,
                                   f"bad coordinator receipt JSON line {number}")
        step = item.get("step") if isinstance(item, dict) else None
        expected = expected_coordinator.get(step) if type(step) is int else None
        if (expected is None or step in coordinator_steps
                or item.get("schema_version") != "fate.checkpoint.coordinator.v1"
                or item.get("session_id") != session_id or item.get("tree_id") != tree_id
                or item.get("previous_policy_version") != expected[0]
                or item.get("policy_version") != expected[1]
                or item.get("ack_sha256") != expected[2]
                or type(item.get("coordinator_run_id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", item["coordinator_run_id"])):
            return CheckpointAudit(False, len(checkpoints), len(observed_acks), version,
                                   f"coordinator receipt binding failure at line {number}")
        coordinator_steps.add(step)
    if coordinator_steps != set(checkpoints):
        return CheckpointAudit(False, len(checkpoints), len(observed_acks), version,
                               "coordinator receipt coverage mismatch")
    return CheckpointAudit(True, len(checkpoints), len(observed_acks), version, "all session-scoped checkpoint ACKs are bound and observed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--tree-id", required=True)
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--previous-policy-version", type=int, required=True)
    parser.add_argument("--policy-version", type=int, required=True)
    parser.add_argument("--coordinator-run-id", required=True)
    parser.add_argument("--model-sha256", required=True)
    args = parser.parse_args()
    path = write_checkpoint_ack(
        args.checkpoint_dir, session_id=args.session_id, tree_id=args.tree_id,
        step=args.step, previous_policy_version=args.previous_policy_version,
        policy_version=args.policy_version, coordinator_run_id=args.coordinator_run_id,
        model_sha256=args.model_sha256,
    )
    print(json.dumps({"ack": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

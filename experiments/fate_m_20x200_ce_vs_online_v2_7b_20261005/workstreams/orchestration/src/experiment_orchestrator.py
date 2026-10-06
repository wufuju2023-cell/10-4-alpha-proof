#!/usr/bin/env python3
"""Fail-closed experiment stage supervisor.

The ModelScope UI observation is the only storage-capacity authority. This
module intentionally never calls ``df`` or ``shutil.disk_usage``.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence


UTC = dt.timezone.utc
STAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTROL_POLL_SECONDS = 1.0


class ConfigError(ValueError):
    pass


class StorageGuardError(RuntimeError):
    pass


class ReceiptError(RuntimeError):
    pass


class LockError(RuntimeError):
    pass


class _SkipDone(Exception):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_prefix(path: Path, size_bytes: int) -> str:
    digest = hashlib.sha256()
    remaining = size_bytes
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                raise ReceiptError(f"file shorter than committed prefix: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if not existed:
        _fsync_directory(path.parent)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def validate_config(config: Any) -> dict[str, Any]:
    _expect(isinstance(config, dict), "config must be an object")
    allowed = {
        "schema_version", "experiment_id", "experiment_root", "run_root", "emergency_dir",
        "control_reserve_bytes", "heartbeat_seconds", "storage_guard", "immutable_inputs",
        "frozen_environment", "stages",
    }
    _expect(set(config) == allowed, f"top-level keys must be exactly {sorted(allowed)}")
    _expect(config["schema_version"] == 2, "schema_version must be 2")
    for key in ("experiment_id", "experiment_root", "run_root", "emergency_dir"):
        _expect(isinstance(config[key], str) and bool(config[key]), f"{key} must be non-empty")
    heartbeat = config["heartbeat_seconds"]
    _expect(isinstance(heartbeat, int) and not isinstance(heartbeat, bool) and 20 <= heartbeat <= 30,
            "heartbeat_seconds must be an integer in [20, 30]")
    reserve = config["control_reserve_bytes"]
    _expect(isinstance(reserve, int) and 65536 <= reserve <= 64 * 1024 * 1024,
            "control_reserve_bytes must be in [65536, 67108864]")

    guard = config["storage_guard"]
    guard_keys = {
        "observation_file", "required_source", "expected_capacity_gib", "max_age_seconds",
        "warn_used_gib", "stop_used_gib",
    }
    _expect(isinstance(guard, dict) and set(guard) == guard_keys,
            "storage_guard must contain exactly the documented keys")
    _expect(guard["required_source"] == "modelscope_ui_top_right",
            "required_source must be modelscope_ui_top_right")
    _expect(guard["expected_capacity_gib"] == 100, "expected_capacity_gib is frozen at 100")
    _expect(isinstance(guard["max_age_seconds"], int) and 20 <= guard["max_age_seconds"] <= 60,
            "max_age_seconds must be in [20, 60]")
    _expect(guard["warn_used_gib"] == 90 and guard["stop_used_gib"] == 95,
            "storage thresholds are frozen at 90/95 GiB")

    inputs = config["immutable_inputs"]
    _expect(isinstance(inputs, list) and bool(inputs), "immutable_inputs must be non-empty")
    input_ids: set[str] = set()
    for index, artifact in enumerate(inputs):
        _expect(isinstance(artifact, dict) and set(artifact) == {"id", "path", "sha256"},
                f"immutable_inputs[{index}] must contain id/path/sha256")
        _expect(isinstance(artifact["id"], str) and artifact["id"] not in input_ids,
                f"immutable input id must be unique at index {index}")
        input_ids.add(artifact["id"])
        _expect(isinstance(artifact["path"], str) and bool(artifact["path"]), "input path must be non-empty")
        _expect(isinstance(artifact["sha256"], str) and SHA256_RE.fullmatch(artifact["sha256"]) is not None,
                f"immutable_inputs[{index}].sha256 must be lowercase SHA-256")

    frozen_env = config["frozen_environment"]
    _expect(isinstance(frozen_env, dict) and set(frozen_env) == {"inherit", "set"},
            "frozen_environment must contain inherit/set")
    _expect(isinstance(frozen_env["inherit"], list) and
            all(isinstance(v, str) and v for v in frozen_env["inherit"]), "inherit must be a string list")
    _expect(len(frozen_env["inherit"]) == len(set(frozen_env["inherit"])), "inherited environment keys must be unique")
    _expect(isinstance(frozen_env["set"], dict) and
            all(isinstance(k, str) and isinstance(v, str) for k, v in frozen_env["set"].items()),
            "frozen_environment.set must map strings to strings")

    stages = config["stages"]
    _expect(isinstance(stages, list) and bool(stages), "stages must be non-empty")
    stage_ids: set[str] = set()
    stage_keys = {
        "id", "command", "budget_seconds", "expensive", "cwd", "max_additional_gib",
        "resume_contract", "success_receipt", "required_outputs", "progress_file",
    }
    for index, stage in enumerate(stages):
        prefix = f"stages[{index}]"
        _expect(isinstance(stage, dict) and set(stage) == stage_keys, f"{prefix} has missing or unknown keys")
        stage_id = stage["id"]
        _expect(isinstance(stage_id, str) and STAGE_ID_RE.fullmatch(stage_id) is not None,
                f"{prefix}.id is invalid")
        _expect(stage_id not in stage_ids, f"duplicate stage id: {stage_id}")
        stage_ids.add(stage_id)
        _expect(isinstance(stage["command"], list) and bool(stage["command"]) and
                all(isinstance(v, str) and v for v in stage["command"]), f"{prefix}.command is invalid")
        _expect(isinstance(stage["budget_seconds"], int) and stage["budget_seconds"] > 0,
                f"{prefix}.budget_seconds must be positive")
        _expect(isinstance(stage["expensive"], bool), f"{prefix}.expensive must be boolean")
        _expect(isinstance(stage["cwd"], str) and isinstance(stage["progress_file"], str),
                f"{prefix} cwd/progress_file must be strings")
        growth = stage["max_additional_gib"]
        _expect(isinstance(growth, (int, float)) and not isinstance(growth, bool) and math.isfinite(growth) and growth >= 0,
                f"{prefix}.max_additional_gib must be finite and non-negative")
        contract = stage["resume_contract"]
        _expect(isinstance(contract, dict) and contract.get("mode") in {"restart_idempotent", "checkpoint"},
                f"{prefix}.resume_contract mode is invalid")
        if contract["mode"] == "restart_idempotent":
            _expect(set(contract) == {"mode"}, f"{prefix} restart contract has unknown keys")
            _expect(not stage["expensive"], f"{prefix}: expensive stages require checkpoint resume")
        else:
            _expect(set(contract) == {"mode", "checkpoint_path", "receipt_path", "event_log_path", "resume_args", "idempotent"},
                    f"{prefix} checkpoint contract is incomplete")
            _expect(contract["idempotent"] is True, f"{prefix} checkpoint continuation must be idempotent")
            _expect(all(isinstance(contract[k], str) and contract[k]
                        for k in ("checkpoint_path", "receipt_path", "event_log_path")),
                    f"{prefix} checkpoint paths must be non-empty")
            _expect(isinstance(contract["resume_args"], list) and
                    all(isinstance(v, str) for v in contract["resume_args"]) and
                    any("{checkpoint}" in v for v in contract["resume_args"]),
                    f"{prefix}.resume_args must include {{checkpoint}}")
        if stage["expensive"]:
            _expect(growth > 0, f"{prefix}: expensive stage must reserve positive max_additional_gib")
        _expect(isinstance(stage["success_receipt"], str) and stage["success_receipt"],
                f"{prefix}.success_receipt must be non-empty")
        outputs = stage["required_outputs"]
        _expect(isinstance(outputs, list) and bool(outputs), f"{prefix}.required_outputs must be non-empty")
        output_ids: set[str] = set()
        for output in outputs:
            _expect(isinstance(output, dict) and set(output) == {"id", "path"}, f"{prefix} output must contain id/path")
            _expect(output["id"] not in output_ids and isinstance(output["id"], str), f"{prefix} output ids must be unique")
            output_ids.add(output["id"])
            _expect(isinstance(output["path"], str) and output["path"], f"{prefix} output path is invalid")
    return config


def config_root(config_path: Path, config: Mapping[str, Any]) -> Path:
    candidate = Path(config["experiment_root"])
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def resolve_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def read_storage_observation(path: Path, guard: Mapping[str, Any], *, now: dt.datetime | None = None) -> dict[str, Any]:
    if not path.exists():
        raise StorageGuardError(f"missing UI storage observation: {path}")
    try:
        observation = load_json(path)
        observed_at = parse_timestamp(observation["observed_at"])
        used = float(observation["used_gib"])
        capacity = float(observation["capacity_gib"])
        source = observation["source"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageGuardError(f"invalid UI storage observation: {exc}") from exc
    if not (math.isfinite(used) and math.isfinite(capacity)):
        raise StorageGuardError("storage values must be finite")
    if source != guard["required_source"]:
        raise StorageGuardError(f"storage source {source!r} is not authoritative")
    if capacity != float(guard["expected_capacity_gib"]):
        raise StorageGuardError(f"UI capacity {capacity} does not match frozen {guard['expected_capacity_gib']} GiB quota")
    if not (0 <= used <= capacity):
        raise StorageGuardError(f"invalid storage values: used={used}, capacity={capacity}")
    age = ((now or utc_now()) - observed_at).total_seconds()
    if age < -60:
        raise StorageGuardError(f"UI observation is from the future by {-age:.1f}s")
    if age > guard["max_age_seconds"]:
        raise StorageGuardError(f"UI observation stale: {age:.1f}s > {guard['max_age_seconds']}s")
    severity = "stop" if used >= guard["stop_used_gib"] else "warning" if used >= guard["warn_used_gib"] else "ok"
    return {**observation, "used_gib": used, "capacity_gib": capacity,
            "age_seconds": round(max(age, 0), 3), "severity": severity}


def storage_admission(storage: Mapping[str, Any], guard: Mapping[str, Any], stage: Mapping[str, Any],
                      envelope: Mapping[str, float] | None = None) -> dict[str, float]:
    used = float(storage["used_gib"])
    if used >= guard["stop_used_gib"]:
        raise StorageGuardError(f"storage hard stop: {used:.3f} GiB")
    if envelope is None:
        projected_peak = used + float(stage["max_additional_gib"])
        if projected_peak >= guard["stop_used_gib"]:
            raise StorageGuardError(
                f"insufficient reserved headroom: {used:.3f} + {stage['max_additional_gib']:.3f} >= {guard['stop_used_gib']} GiB"
            )
        if stage["expensive"] and used >= guard["warn_used_gib"]:
            raise StorageGuardError(f"expensive stage refused in warning region: {used:.3f} GiB")
        return {"baseline_used_gib": used, "max_additional_gib": float(stage["max_additional_gib"]),
                "projected_peak_gib": projected_peak}
    projected_peak = float(envelope["projected_peak_gib"])
    if used > projected_peak + 1e-6:
        raise StorageGuardError(
            f"stage exceeded sealed projected peak: observed {used:.3f} > {projected_peak:.3f} GiB"
        )
    return dict(envelope)


def write_storage_observation(path: Path, *, used_gib: float, capacity_gib: float, source: str,
                              observed_at: str | None = None) -> dict[str, Any]:
    if source != "modelscope_ui_top_right":
        raise StorageGuardError("source must be modelscope_ui_top_right")
    if not all(math.isfinite(v) for v in (used_gib, capacity_gib)) or not (0 <= used_gib <= capacity_gib):
        raise StorageGuardError("storage values must be finite and satisfy 0 <= used <= capacity")
    timestamp = observed_at or iso_now()
    parse_timestamp(timestamp)
    value = {"schema_version": 1, "source": source, "observed_at": timestamp,
             "used_gib": float(used_gib), "capacity_gib": float(capacity_gib),
             "free_gib": round(capacity_gib - used_gib, 6)}
    atomic_json(path, value)
    return value


def gpu_metrics() -> dict[str, Any]:
    try:
        result = subprocess.run(["rocm-smi", "--showuse", "--showmemuse", "--showtemp", "--json"],
                                capture_output=True, text=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "source": "rocm-smi", "reason": type(exc).__name__}
    if result.returncode:
        return {"available": False, "source": "rocm-smi", "returncode": result.returncode}
    try:
        return {"available": True, "source": "rocm-smi", "devices": json.loads(result.stdout)}
    except json.JSONDecodeError:
        return {"available": True, "source": "rocm-smi", "raw": result.stdout[:2000]}


def read_progress(path: Path) -> dict[str, Any]:
    empty = {"completed": None, "total": None, "unit": None, "aggregate_rate": None}
    if not path.exists():
        return empty
    try:
        value = load_json(path)
        return {key: value.get(key) for key in empty}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {**empty, "error": "unreadable_progress_file"}


def emit(event: Mapping[str, Any]) -> None:
    print(json.dumps(event, sort_keys=True, ensure_ascii=False), flush=True)


def _boot_id() -> str:
    if os.name == "posix":
        with contextlib.suppress(OSError):
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    boot_epoch_minutes = round((time.time() - kernel32.GetTickCount64() / 1000.0) / 60.0)
    return f"windows:{boot_epoch_minutes}"


def _process_identity(pid: int) -> str | None:
    if os.name == "posix":
        try:
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
            return fields[21]
        except (OSError, IndexError):
            return None
    from ctypes import wintypes
    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
    try:
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(ticks)
    finally:
        kernel32.CloseHandle(handle)


def _group_alive(pgid: int | None) -> bool:
    if pgid is None or os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class StageLease:
    def __init__(self, lock_dir: Path, stage_id: str):
        self.lock_dir = lock_dir
        self.lease_path = lock_dir / "lease.json"
        self.stage_id = stage_id
        self.data: dict[str, Any] = {}
        self.acquired = False

    def _owner_alive(self, lease: Mapping[str, Any]) -> bool:
        if lease.get("hostname") != socket.gethostname() or lease.get("boot_id") != _boot_id():
            return False
        pid = lease.get("owner_pid")
        return isinstance(pid, int) and _process_identity(pid) == lease.get("owner_identity")

    def acquire(self) -> None:
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_dir.mkdir()
        except FileExistsError:
            try:
                old = load_json(self.lease_path)
            except Exception as exc:
                raise LockError(f"unreadable lock lease; manual inspection required: {self.lock_dir}: {exc}") from exc
            if self._owner_alive(old):
                raise LockError(f"stage is owned by live supervisor pid={old.get('owner_pid')}")
            pgid = old.get("child_pgid")
            if old.get("boot_id") == _boot_id() and isinstance(pgid, int) and _group_alive(pgid):
                raise LockError(f"stale supervisor but owned process group {pgid} is still alive; terminate it before recovery")
            archive = self.lock_dir.with_name(f"{self.lock_dir.name}.recovered-{int(time.time())}-{os.getpid()}")
            os.replace(self.lock_dir, archive)
            _fsync_directory(self.lock_dir.parent)
            self.lock_dir.mkdir()
        self.data = {"schema_version": 1, "stage": self.stage_id, "hostname": socket.gethostname(),
                     "boot_id": _boot_id(), "owner_pid": os.getpid(),
                     "owner_identity": _process_identity(os.getpid()), "child_pid": None,
                     "child_pgid": None, "acquired_at": iso_now(), "lease_updated_at": iso_now()}
        try:
            atomic_json(self.lease_path, self.data)
            self.acquired = True
        except BaseException:
            with contextlib.suppress(OSError):
                self.lock_dir.rmdir()
            raise

    def update_child(self, pid: int, pgid: int | None) -> None:
        self.data.update({"child_pid": pid, "child_pgid": pgid, "lease_updated_at": iso_now()})
        atomic_json(self.lease_path, self.data)

    def heartbeat(self) -> None:
        self.data["lease_updated_at"] = iso_now()
        atomic_json(self.lease_path, self.data)

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            with contextlib.suppress(FileNotFoundError):
                self.lease_path.unlink()
            with contextlib.suppress(FileNotFoundError):
                self.lock_dir.rmdir()
            _fsync_directory(self.lock_dir.parent)
        finally:
            self.acquired = False


class WindowsJob:
    KILL_ON_JOB_CLOSE = 0x00002000

    def __init__(self) -> None:
        self.handle: int | None = None

    def assign(self, process: subprocess.Popen[str]) -> None:
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = int(handle)

    def terminate(self, exit_code: int = 1) -> None:
        if self.handle is not None:
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateJobObject(wintypes.HANDLE(self.handle), exit_code)

    def close(self) -> None:
        if self.handle is not None:
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = None


class ManagedProcess:
    def __init__(self, process: subprocess.Popen[str], pgid: int | None, job: WindowsJob | None,
                 watchdog: subprocess.Popen[Any] | None = None, watchdog_control_fd: int | None = None):
        self.process = process
        self.pgid = pgid
        self.job = job
        self.watchdog = watchdog
        self.watchdog_control_fd = watchdog_control_fd

    def _disarm_watchdog(self) -> None:
        if self.watchdog_control_fd is not None:
            with contextlib.suppress(OSError):
                os.write(self.watchdog_control_fd, b"DISARM\n")
            with contextlib.suppress(OSError):
                os.close(self.watchdog_control_fd)
            self.watchdog_control_fd = None
        if self.watchdog is not None:
            try:
                self.watchdog.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.watchdog.kill()
                self.watchdog.wait()
            self.watchdog = None

    def stop_tree(self, grace_seconds: float = 5.0) -> None:
        if os.name == "nt":
            assert self.job is not None
            self.job.terminate(1)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=grace_seconds)
            self.job.close()
            return
        assert self.pgid is not None
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.pgid, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            self.process.poll()
            if not _group_alive(self.pgid):
                self._disarm_watchdog()
                return
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.pgid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.process.poll()
            if not _group_alive(self.pgid):
                self._disarm_watchdog()
                return
            time.sleep(0.05)
        raise RuntimeError(f"process group {self.pgid} survived SIGKILL")

    def close(self) -> None:
        if os.name == "nt":
            if self.job is not None:
                self.job.close()
        elif self.pgid is not None and _group_alive(self.pgid):
            self.stop_tree(grace_seconds=0.2)
        else:
            self._disarm_watchdog()


def _linux_child_setup(expected_parent: int) -> None:
    os.setsid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGKILL)


def _kill_posix_group(pgid: int, grace_seconds: float = 2.0) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and _group_alive(pgid):
        time.sleep(0.05)
    if _group_alive(pgid):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)


def _watchdog_main(control_fd: int, pgid: int) -> int:
    data = bytearray()
    try:
        while True:
            chunk = os.read(control_fd, 64)
            if not chunk:
                break
            data.extend(chunk)
    finally:
        with contextlib.suppress(OSError):
            os.close(control_fd)
    if bytes(data) != b"DISARM\n":
        # Owner loss is not a graceful shutdown: prevent descendants that
        # ignore SIGTERM from writing any further checkpoint bytes.
        _kill_posix_group(pgid, grace_seconds=0.0)
    return 0


def _stage_wrapper_main(gate_fd: int, command: Sequence[str]) -> int:
    try:
        gate = os.read(gate_fd, 16)
    finally:
        os.close(gate_fd)
    if gate != b"GO\n":
        return 125
    os.execvpe(command[0], list(command), os.environ)
    return 127


def _resume_windows_process_threads(pid: int) -> None:
    from ctypes import wintypes

    snapshot_threads = 0x00000004
    thread_suspend_resume = 0x0002
    invalid_handle = ctypes.c_void_p(-1).value

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(snapshot_threads, 0)
    if int(snapshot) == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = 0
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        present = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while present:
            if entry.th32OwnerProcessID == pid:
                thread = kernel32.OpenThread(thread_suspend_resume, False, entry.th32ThreadID)
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    previous = kernel32.ResumeThread(thread)
                    if previous == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                    resumed += 1
                finally:
                    kernel32.CloseHandle(thread)
            present = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    if resumed != 1:
        raise RuntimeError(f"expected exactly one suspended primary thread, found {resumed}")


def start_managed_process(command: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> ManagedProcess:
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        expected_parent = os.getpid()
        kwargs["preexec_fn"] = lambda: _linux_child_setup(expected_parent)
        gate_read, gate_write = os.pipe()
        wrapper = [sys.executable, str(Path(__file__).resolve()), "_stage-wrapper", str(gate_read), "--", *command]
        process = subprocess.Popen(wrapper, cwd=cwd, env=dict(env), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   bufsize=1, pass_fds=(gate_read,), **kwargs)
        os.close(gate_read)
        watchdog_read, watchdog_write = os.pipe()
        try:
            watchdog = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_watchdog", str(watchdog_read), str(process.pid)],
                pass_fds=(watchdog_read,), close_fds=True, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            os.close(watchdog_read)
            os.write(gate_write, b"GO\n")
            os.close(gate_write)
            return ManagedProcess(process, process.pid, None, watchdog, watchdog_write)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(gate_write)
            with contextlib.suppress(OSError):
                os.close(watchdog_read)
            with contextlib.suppress(OSError):
                os.close(watchdog_write)
            _kill_posix_group(process.pid, grace_seconds=0.1)
            process.wait()
            raise
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004  # CREATE_SUSPENDED
    process = subprocess.Popen(list(command), cwd=cwd, env=dict(env), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                               bufsize=1, **kwargs)
    if os.name == "nt":
        job = WindowsJob()
        try:
            job.assign(process)
            _resume_windows_process_threads(process.pid)
        except BaseException:
            job.terminate(1)
            job.close()
            with contextlib.suppress(Exception):
                process.kill()
            process.wait()
            raise
        return ManagedProcess(process, None, job)
    return ManagedProcess(process, process.pid, None)


def _pump_output(pipe: Any, log_path: Path, result: queue.Queue[BaseException | None]) -> None:
    error: BaseException | None = None
    try:
        with log_path.open("a", encoding="utf-8", newline="\n") as log:
            for line in iter(pipe.readline, ""):
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
    except BaseException as exc:
        error = exc
    finally:
        with contextlib.suppress(Exception):
            pipe.close()
        result.put(error)


def verify_inputs(root: Path, artifacts: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    verified = []
    for artifact in artifacts:
        path = resolve_path(root, artifact["path"])
        if not path.is_file():
            raise ReceiptError(f"immutable input missing or not a file: {path}")
        actual = sha256_file(path)
        if actual != artifact["sha256"]:
            raise ReceiptError(f"immutable input hash mismatch for {artifact['id']}: {actual}")
        verified.append({"id": artifact["id"], "path": str(path), "sha256": actual, "size_bytes": path.stat().st_size})
    return verified


def frozen_environment(spec: Mapping[str, Any]) -> tuple[dict[str, str], list[dict[str, str]]]:
    environment: dict[str, str] = {}
    records: list[dict[str, str]] = []
    for key in spec["inherit"]:
        if key not in os.environ:
            raise ReceiptError(f"required inherited environment variable is missing: {key}")
        environment[key] = os.environ[key]
    environment.update(spec["set"])
    for key in sorted(environment):
        records.append({"key": key, "value_sha256": hashlib.sha256(environment[key].encode()).hexdigest()})
    return environment, records


def bindings(config: Mapping[str, Any], stage: Mapping[str, Any], root: Path) -> tuple[str, str, list[dict[str, Any]], dict[str, str], list[dict[str, str]]]:
    artifacts = verify_inputs(root, config["immutable_inputs"])
    environment, env_records = frozen_environment(config["frozen_environment"])
    input_fingerprint = canonical_hash({"artifacts": artifacts, "environment": env_records})
    stage_fingerprint = canonical_hash({"experiment_id": config["experiment_id"], "stage": stage,
                                        "storage_guard": config["storage_guard"],
                                        "input_fingerprint": input_fingerprint})
    return input_fingerprint, stage_fingerprint, artifacts, environment, env_records


def validate_checkpoint(root: Path, stage: Mapping[str, Any], input_fp: str, stage_fp: str) -> dict[str, Any]:
    contract = stage["resume_contract"]
    checkpoint = resolve_path(root, contract["checkpoint_path"])
    event_log = resolve_path(root, contract["event_log_path"])
    receipt_path = resolve_path(root, contract["receipt_path"])
    try:
        receipt = load_json(receipt_path)
    except Exception as exc:
        raise ReceiptError(f"checkpoint receipt unavailable: {receipt_path}: {exc}") from exc
    required = {"schema_version", "status", "stage", "input_fingerprint", "stage_fingerprint",
                "last_committed_unit", "checkpoint_sha256", "event_log_committed_bytes",
                "event_log_prefix_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ReceiptError("checkpoint receipt fields are incomplete")
    if receipt["schema_version"] != 1 or receipt["status"] != "COMMITTED" or receipt["stage"] != stage["id"]:
        raise ReceiptError("checkpoint receipt identity/status mismatch")
    if receipt["input_fingerprint"] != input_fp or receipt["stage_fingerprint"] != stage_fp:
        raise ReceiptError("checkpoint is bound to different inputs/config")
    if (not isinstance(receipt["last_committed_unit"], int) or
            isinstance(receipt["last_committed_unit"], bool) or receipt["last_committed_unit"] < 0):
        raise ReceiptError("last_committed_unit must be a non-negative integer")
    committed_bytes = receipt["event_log_committed_bytes"]
    if not isinstance(committed_bytes, int) or isinstance(committed_bytes, bool) or committed_bytes < 0:
        raise ReceiptError("event_log_committed_bytes must be a non-negative integer")
    if not checkpoint.is_file() or sha256_file(checkpoint) != receipt["checkpoint_sha256"]:
        raise ReceiptError("checkpoint file is missing or hash-mismatched")
    if not event_log.is_file() or event_log.stat().st_size != committed_bytes:
        raise ReceiptError("event log size differs from the atomically committed boundary")
    if sha256_prefix(event_log, committed_bytes) != receipt["event_log_prefix_sha256"]:
        raise ReceiptError("event log committed prefix hash mismatch")
    return {**receipt, "checkpoint_path": str(checkpoint), "event_log_path": str(event_log),
            "receipt_path": str(receipt_path), "receipt_sha256": sha256_file(receipt_path)}


def seal_checkpoint_evidence(stage_dir: Path, evidence: Mapping[str, Any]) -> dict[str, Any]:
    ledger_path = stage_dir / "checkpoint_ledger.json"
    current = None
    if ledger_path.exists():
        current = load_json(ledger_path)
        if not isinstance(current, dict) or set(current) != {"schema_version", "highest_committed_unit", "evidence", "updated_at"}:
            raise ReceiptError(f"checkpoint ledger is corrupt: {ledger_path}")
        previous_unit = current["highest_committed_unit"]
        new_unit = evidence["last_committed_unit"]
        if new_unit < previous_unit:
            raise ReceiptError(f"checkpoint rollback rejected: {new_unit} < sealed {previous_unit}")
        if new_unit == previous_unit and evidence != current["evidence"]:
            raise ReceiptError(f"checkpoint fork rejected at committed unit {new_unit}")
        if new_unit == previous_unit:
            return current
    ledger = {"schema_version": 1, "highest_committed_unit": evidence["last_committed_unit"],
              "evidence": dict(evidence), "updated_at": iso_now()}
    atomic_json(ledger_path, ledger)
    return ledger


def validate_success(root: Path, stage: Mapping[str, Any], input_fp: str, stage_fp: str) -> dict[str, Any]:
    receipt_path = resolve_path(root, stage["success_receipt"])
    try:
        receipt = load_json(receipt_path)
    except Exception as exc:
        raise ReceiptError(f"success receipt unavailable: {receipt_path}: {exc}") from exc
    required = {"schema_version", "status", "stage", "input_fingerprint", "stage_fingerprint", "outputs"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ReceiptError("success receipt fields are incomplete")
    if (receipt["schema_version"], receipt["status"], receipt["stage"]) != (1, "DONE", stage["id"]):
        raise ReceiptError("success receipt identity/status mismatch")
    if receipt["input_fingerprint"] != input_fp or receipt["stage_fingerprint"] != stage_fp:
        raise ReceiptError("success receipt is bound to different inputs/config")
    if not isinstance(receipt["outputs"], dict):
        raise ReceiptError("success receipt outputs must be an object")
    verified = []
    for declared in stage["required_outputs"]:
        output = receipt["outputs"].get(declared["id"])
        path = resolve_path(root, declared["path"])
        if not isinstance(output, dict) or set(output) != {"sha256", "size_bytes"}:
            raise ReceiptError(f"missing output receipt: {declared['id']}")
        if not path.is_file():
            raise ReceiptError(f"required output missing: {path}")
        actual = sha256_file(path)
        if output["sha256"] != actual or output["size_bytes"] != path.stat().st_size:
            raise ReceiptError(f"required output mismatch: {declared['id']}")
        verified.append({"id": declared["id"], "path": str(path), "sha256": actual,
                         "size_bytes": path.stat().st_size})
    return {"receipt_path": str(receipt_path), "receipt_sha256": sha256_file(receipt_path), "outputs": verified}


def allocate_attempt(stage_dir: Path) -> int:
    counter = stage_dir / "next_attempt.json"
    current = 0
    if counter.exists():
        value = load_json(counter)
        if not isinstance(value, dict) or not isinstance(value.get("last_allocated"), int):
            raise ReceiptError(f"corrupt attempt counter: {counter}")
        current = value["last_allocated"]
    allocated = current + 1
    atomic_json(counter, {"last_allocated": allocated, "updated_at": iso_now()})
    return allocated


def create_control_reserve(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        chunk = b"\0" * min(size, 1024 * 1024)
        remaining = size
        while remaining:
            piece = chunk[:min(len(chunk), remaining)]
            handle.write(piece)
            remaining -= len(piece)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def emergency_receipt(directory: Path, record: Mapping[str, Any], errors: Sequence[str]) -> Path | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record['experiment_id']}-{record['stage']}-attempt{record['attempt']}-{time.time_ns()}.emergency.json"
        payload = {"schema_version": 1, "event": "finalization-emergency", "authoritative": True,
                   "created_at": iso_now(), "errors": list(errors), "record": record}
        data = (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8", "replace")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(directory)
        return path
    except BaseException as exc:
        os.write(2, f"FATAL: emergency receipt failed: {exc}\n".encode("utf-8", "replace"))
        return None


def latest_authoritative_emergency(directory: Path, experiment_id: str, stage_id: str) -> dict[str, Any] | None:
    latest: tuple[int, int, dict[str, Any]] | None = None
    if not directory.exists():
        return None
    for path in directory.glob(f"{experiment_id}-{stage_id}-attempt*.emergency.json"):
        try:
            payload = load_json(path)
            if payload.get("event") != "finalization-emergency" or payload.get("authoritative") is not True:
                continue
            record = payload["record"]
            key = (int(record.get("attempt", -1)), path.stat().st_mtime_ns)
            if latest is None or key > latest[:2]:
                latest = (key[0], key[1], {**payload, "path": str(path)})
        except Exception:
            continue
    return latest[2] if latest else None


def finalize_record(record: dict[str, Any], state_path: Path, attempts_path: Path,
                    reserve_path: Path, emergency_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        reserve_path.unlink()
        _fsync_directory(reserve_path.parent)
    except FileNotFoundError:
        pass
    except BaseException as exc:
        errors.append(f"reserve:{type(exc).__name__}:{exc}")
    if errors:
        record["status"] = "FAILED"
        record["failure_reason"] = "finalization_failed:" + errors[-1]
    try:
        atomic_json(state_path, record)
    except BaseException as exc:
        errors.append(f"state:{type(exc).__name__}:{exc}")
        record["status"] = "FAILED"
        record["failure_reason"] = "finalization_failed:" + errors[-1]
        emergency = emergency_receipt(emergency_dir, record, errors)
        if emergency is not None:
            errors.append(f"authoritative_emergency:{emergency}")
        return errors
    try:
        append_jsonl(attempts_path, record)
    except BaseException as exc:
        errors.append(f"attempts:{type(exc).__name__}:{exc}")
        record["status"] = "FAILED"
        record["failure_reason"] = "finalization_failed:" + errors[-1]
        try:
            atomic_json(state_path, record)
        except BaseException as state_exc:
            errors.append(f"failed_state_rewrite:{type(state_exc).__name__}:{state_exc}")
    if errors:
        emergency = emergency_receipt(emergency_dir, record, errors)
        if emergency is not None:
            errors.append(f"authoritative_emergency:{emergency}")
    return errors


def run_stage(config_path: Path, config: Mapping[str, Any], stage: Mapping[str, Any], *,
              resume: bool, restart: bool = False) -> int:
    root = config_root(config_path, config)
    run_root = resolve_path(root, config["run_root"])
    stage_dir = run_root / "stages" / stage["id"]
    state_path, attempts_path = stage_dir / "state.json", stage_dir / "attempts.jsonl"
    process_log = stage_dir / "process.log"
    lock = StageLease(stage_dir / ".lock", stage["id"])
    managed: ManagedProcess | None = None
    pump: threading.Thread | None = None
    pump_result: queue.Queue[BaseException | None] = queue.Queue()
    reserve_path = stage_dir / ".control-reserve"
    emergency_dir = resolve_path(root, config["emergency_dir"])
    outstanding_emergency = latest_authoritative_emergency(emergency_dir, config["experiment_id"], stage["id"])
    if outstanding_emergency is not None:
        emit({"event": "authoritative-emergency-block", "stage": stage["id"],
              "emergency": outstanding_emergency["path"], "time": iso_now()})
        return 1
    signal_event = threading.Event()
    signal_name: list[str] = []
    old_handlers: dict[int, Any] = {}
    started_mono = time.monotonic()
    record: dict[str, Any] = {"experiment_id": config["experiment_id"], "stage": stage["id"], "attempt": 0,
                              "status": "FAILED", "failure_reason": "supervisor_setup_incomplete"}
    return_code = 1
    finalization_errors: list[str] = []
    lock_error: str | None = None
    skip_existing_done = False
    lock.acquire()
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        create_control_reserve(reserve_path, config["control_reserve_bytes"])
        input_fp, stage_fp, input_records, environment, env_records = bindings(config, stage, root)
        prior = load_json(state_path) if state_path.exists() else None
        if prior is not None and prior.get("status") == "DONE":
            # Never overwrite a previously durable DONE merely because a later
            # validation detects changed inputs or missing outputs.
            skip_existing_done = True
            record = dict(prior)
            if not resume:
                raise ConfigError("stage is already DONE; use --resume to validate and skip")
            if prior.get("stage_fingerprint") != stage_fp or prior.get("input_fingerprint") != input_fp:
                raise ReceiptError("DONE receipt no longer matches immutable inputs/config/environment")
            current_evidence = validate_success(root, stage, input_fp, stage_fp)
            if current_evidence != prior.get("output_evidence"):
                raise ReceiptError("DONE output/success-receipt evidence differs from the orchestrator-sealed evidence")
            emit({"event": "stage-resume-skip", "stage": stage["id"], "status": "DONE", "time": iso_now()})
            raise _SkipDone
        if prior is not None and not (resume or restart):
            raise ConfigError("interrupted/failed state exists; explicitly choose --resume or --restart")
        if resume and restart:
            raise ConfigError("--resume and --restart are mutually exclusive")

        attempt = allocate_attempt(stage_dir)
        record.update({"schema_version": 2, "attempt": attempt, "started_at": iso_now(),
                       "input_fingerprint": input_fp, "stage_fingerprint": stage_fp})

        command = list(stage["command"])
        resume_from = None
        if prior is not None and resume:
            contract = stage["resume_contract"]
            if contract["mode"] == "checkpoint":
                resume_from = validate_checkpoint(root, stage, input_fp, stage_fp)
                seal_checkpoint_evidence(stage_dir, resume_from)
                command.extend(arg.replace("{checkpoint}", resume_from["checkpoint_path"])
                               for arg in contract["resume_args"])
            elif contract["mode"] != "restart_idempotent":
                raise ReceiptError("stage does not declare a safe resume contract")

        guard = config["storage_guard"]
        observation_path = resolve_path(root, guard["observation_file"])
        storage = read_storage_observation(observation_path, guard)
        storage_envelope = storage_admission(storage, guard, stage)
        record = {"schema_version": 2, "experiment_id": config["experiment_id"], "stage": stage["id"],
                  "attempt": attempt, "status": "RUNNING", "started_at": iso_now(), "ended_at": None,
                  "elapsed_seconds": None, "input_fingerprint": input_fp, "stage_fingerprint": stage_fp,
                  "immutable_inputs": input_records, "environment_hashes": env_records, "command": command,
                  "budget_seconds": stage["budget_seconds"], "expensive": stage["expensive"],
                  "max_additional_gib": stage["max_additional_gib"], "resume_from": resume_from,
                  "checkpoint_evidence": None,
                  "storage_at_start": storage, "storage_envelope": storage_envelope,
                  "storage_at_end": None, "output_evidence": None,
                  "exit_code": None, "failure_reason": None}
        atomic_json(state_path, record)

        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            if sig in old_handlers:
                continue
            old_handlers[sig] = signal.getsignal(sig)
            def handler(received: int, _frame: Any) -> None:
                signal_name[:] = [signal.Signals(received).name]
                signal_event.set()
            signal.signal(sig, handler)

        environment.update({"EXPERIMENT_INPUT_FINGERPRINT": input_fp,
                            "EXPERIMENT_STAGE_FINGERPRINT": stage_fp,
                            "EXPERIMENT_STAGE_ID": stage["id"]})
        managed = start_managed_process(command, cwd=resolve_path(root, stage["cwd"]), env=environment)
        lock.update_child(managed.process.pid, managed.pgid)
        assert managed.process.stdout is not None
        pump = threading.Thread(target=_pump_output, args=(managed.process.stdout, process_log, pump_result), daemon=True)
        pump.start()
        emit({"event": "stage-start", "stage": stage["id"], "attempt": attempt,
              "pid": managed.process.pid, "pgid": managed.pgid, "time": iso_now()})

        deadline = started_mono + stage["budget_seconds"]
        next_heartbeat = time.monotonic() + config["heartbeat_seconds"]
        checkpoint_receipt = (resolve_path(root, stage["resume_contract"]["receipt_path"])
                              if stage["resume_contract"]["mode"] == "checkpoint" else None)
        checkpoint_signature = None
        if checkpoint_receipt is not None and checkpoint_receipt.exists():
            checkpoint_signature = (checkpoint_receipt.stat().st_mtime_ns, checkpoint_receipt.stat().st_size)
        reason = None
        while managed.process.poll() is None:
            now_mono = time.monotonic()
            if signal_event.is_set():
                reason = f"supervisor_signal:{signal_name[0] if signal_name else 'unknown'}"
                managed.stop_tree()
                break
            if now_mono >= deadline:
                reason = "budget_timeout"
                managed.stop_tree()
                break
            try:
                storage = read_storage_observation(observation_path, guard)
                storage_admission(storage, guard, stage, storage_envelope)
            except StorageGuardError as exc:
                reason = f"storage_guard:{exc}"
                managed.stop_tree()
                break
            if checkpoint_receipt is not None and checkpoint_receipt.exists():
                current = (checkpoint_receipt.stat().st_mtime_ns, checkpoint_receipt.stat().st_size)
                if current != checkpoint_signature:
                    checkpoint_signature = current
                    storage = read_storage_observation(observation_path, guard)
                    storage_admission(storage, guard, stage, storage_envelope)
                    emit({"event": "checkpoint-observed", "stage": stage["id"], "storage": storage, "time": iso_now()})
            if now_mono >= next_heartbeat:
                elapsed = now_mono - started_mono
                lock.heartbeat()
                emit({"event": "heartbeat", "stage": stage["id"], "attempt": attempt,
                      "elapsed_seconds": round(elapsed, 3),
                      "remaining_budget_seconds": max(0, round(stage["budget_seconds"] - elapsed, 3)),
                      "progress": read_progress(resolve_path(root, stage["progress_file"])),
                      "gpu": gpu_metrics(), "storage": storage, "time": iso_now()})
                next_heartbeat += config["heartbeat_seconds"]
            time.sleep(CONTROL_POLL_SECONDS)

        exit_code = managed.process.wait()
        record["exit_code"] = exit_code
        if pump is not None:
            pump.join(timeout=10)
            if pump.is_alive():
                raise RuntimeError("output pump did not finish")
            pump_error = pump_result.get_nowait()
            if pump_error is not None:
                raise RuntimeError(f"output pump failed: {pump_error}")
        managed.close()
        if checkpoint_receipt is not None and checkpoint_receipt.exists():
            checkpoint_evidence = validate_checkpoint(root, stage, input_fp, stage_fp)
            seal_checkpoint_evidence(stage_dir, checkpoint_evidence)
            record["checkpoint_evidence"] = checkpoint_evidence
        record["storage_at_end"] = read_storage_observation(observation_path, guard)
        storage_admission(record["storage_at_end"], guard, stage, storage_envelope)
        if reason:
            raise RuntimeError(reason)
        if exit_code != 0:
            raise RuntimeError(f"child_exit_code:{exit_code}")
        record["output_evidence"] = validate_success(root, stage, input_fp, stage_fp)
        record["status"] = "DONE"
        record["failure_reason"] = None
        return_code = 0
    except _SkipDone:
        return_code = 0
    except BaseException as exc:
        if managed is not None:
            with contextlib.suppress(BaseException):
                managed.stop_tree()
            record["exit_code"] = managed.process.poll()
        record["status"] = "FAILED"
        record["failure_reason"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
        return_code = 1
    finally:
        for sig, old in old_handlers.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, old)
        if skip_existing_done:
            with contextlib.suppress(OSError):
                reserve_path.unlink()
        else:
            record["ended_at"] = iso_now()
            record["elapsed_seconds"] = round(time.monotonic() - started_mono, 3)
            finalization_errors = finalize_record(record, state_path, attempts_path, reserve_path, emergency_dir)
        try:
            lock.release()
        except BaseException as exc:
            lock_error = f"{type(exc).__name__}:{exc}"
            finalization_errors.append(f"lock:{lock_error}")
            emergency_receipt(emergency_dir, record, finalization_errors)
        try:
            emit({"event": "stage-end", "stage": stage["id"], "attempt": record.get("attempt"),
                  "status": record["status"], "failure_reason": record.get("failure_reason"),
                  "finalization_errors": finalization_errors, "time": iso_now()})
        except BaseException as exc:
            os.write(2, f"stage-end emit failed: {exc}\n".encode("utf-8", "replace"))
    return 1 if finalization_errors or lock_error else return_code


def find_stage(config: Mapping[str, Any], stage_id: str) -> Mapping[str, Any]:
    for stage in config["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise ConfigError(f"unknown stage: {stage_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("validate", "status", "recover-lock"):
        item = actions.add_parser(action)
        item.add_argument("--config", required=True, type=Path)
        if action == "recover-lock":
            item.add_argument("--stage", required=True)
    run = actions.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--stage", required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--restart", action="store_true")
    sample = actions.add_parser("storage-sample")
    sample.add_argument("--state-dir", required=True, type=Path)
    sample.add_argument("--used-gib", required=True, type=float)
    sample.add_argument("--capacity-gib", required=True, type=float)
    sample.add_argument("--source", required=True)
    sample.add_argument("--observed-at")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "storage-sample":
            path = args.state_dir.resolve() / "storage_observation.json"
            value = write_storage_observation(path, used_gib=args.used_gib, capacity_gib=args.capacity_gib,
                                                source=args.source, observed_at=args.observed_at)
            emit({"event": "storage-sample-recorded", "path": str(path), "observation": value})
            return 0
        config_path = args.config.resolve()
        config = validate_config(load_json(config_path))
        root = config_root(config_path, config)
        if args.action == "validate":
            verify_inputs(root, config["immutable_inputs"])
            frozen_environment(config["frozen_environment"])
            emit({"event": "config-valid", "config": str(config_path), "config_hash": canonical_hash(config)})
            return 0
        if args.action == "status":
            run_root = resolve_path(root, config["run_root"])
            states = []
            for stage in config["stages"]:
                path = run_root / "stages" / stage["id"] / "state.json"
                emergency = latest_authoritative_emergency(resolve_path(root, config["emergency_dir"]),
                                                           config["experiment_id"], stage["id"])
                if emergency is not None:
                    states.append({**emergency["record"], "authoritative_source": emergency["path"]})
                    continue
                try:
                    states.append(load_json(path) if path.exists() else {"stage": stage["id"], "status": "NOT_STARTED"})
                except Exception as exc:
                    states.append({"stage": stage["id"], "status": "CORRUPT", "error": str(exc)})
            emit({"event": "status", "experiment_id": config["experiment_id"], "stages": states})
            return 0
        stage = find_stage(config, args.stage)
        if args.action == "recover-lock":
            lock = StageLease(resolve_path(root, config["run_root"]) / "stages" / stage["id"] / ".lock", stage["id"])
            lock.acquire()
            lock.release()
            emit({"event": "lock-recovered", "stage": stage["id"]})
            return 0
        return run_stage(config_path, config, stage, resume=args.resume, restart=args.restart)
    except (ConfigError, StorageGuardError, ReceiptError, LockError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        emit({"event": "orchestrator-error", "error": f"{type(exc).__name__}: {exc}", "time": iso_now()})
        return 2


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "_watchdog":
        raise SystemExit(_watchdog_main(int(sys.argv[2]), int(sys.argv[3])))
    if len(sys.argv) >= 5 and sys.argv[1] == "_stage-wrapper":
        separator = sys.argv.index("--", 3)
        raise SystemExit(_stage_wrapper_main(int(sys.argv[2]), sys.argv[separator + 1:]))
    raise SystemExit(main())

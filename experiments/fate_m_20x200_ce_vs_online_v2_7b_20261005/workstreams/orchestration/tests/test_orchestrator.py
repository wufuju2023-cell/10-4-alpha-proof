import datetime as dt
import errno
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "experiment_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("experiment_orchestrator", MODULE_PATH)
orchestrator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = orchestrator
SPEC.loader.exec_module(orchestrator)


WORKER = r'''
import argparse, hashlib, json, os, sys, time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--root", required=True)
p.add_argument("--mode", required=True)
p.add_argument("--resume-from")
a = p.parse_args()
root = Path(a.root)
out = root / "artifacts" / "output.txt"
success = root / "artifacts" / "success.json"
checkpoint = root / "artifacts" / "checkpoint.txt"
checkpoint_receipt = root / "artifacts" / "checkpoint.json"
events = root / "artifacts" / "events.txt"
for path in (out, success, checkpoint, checkpoint_receipt, events): path.parent.mkdir(parents=True, exist_ok=True)
input_fp = os.environ["EXPERIMENT_INPUT_FINGERPRINT"]
stage_fp = os.environ["EXPERIMENT_STAGE_FINGERPRINT"]
stage = os.environ["EXPERIMENT_STAGE_ID"]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def done(path):
    success.write_text(json.dumps({"schema_version":1,"status":"DONE","stage":stage,
        "input_fingerprint":input_fp,"stage_fingerprint":stage_fp,
        "outputs":{"result":{"sha256":sha(path),"size_bytes":path.stat().st_size}}}), encoding="utf-8")
def commit(unit):
    checkpoint.write_text(str(unit), encoding="utf-8")
    checkpoint_receipt.write_text(json.dumps({"schema_version":1,"status":"COMMITTED","stage":stage,
        "input_fingerprint":input_fp,"stage_fingerprint":stage_fp,"last_committed_unit":unit,
        "checkpoint_sha256":sha(checkpoint),"event_log_committed_bytes":events.stat().st_size,
        "event_log_prefix_sha256":sha(events)}), encoding="utf-8")
if a.mode == "sleep": time.sleep(30)
if a.mode == "missing": sys.exit(0)
if a.mode == "mismatch":
    out.write_text("actual", encoding="utf-8"); done(out)
    receipt=json.loads(success.read_text()); receipt["outputs"]["result"]["sha256"]="0"*64
    success.write_text(json.dumps(receipt), encoding="utf-8"); sys.exit(0)
if a.mode == "checkpoint":
    if a.resume_from:
        if Path(a.resume_from).read_text(encoding="utf-8") != "1": sys.exit(31)
        if events.read_text(encoding="utf-8").splitlines() != ["1"]: sys.exit(32)
        with events.open("a", encoding="utf-8") as h: h.write("2\n")
        commit(2)
        out.write_bytes(events.read_bytes()); done(out); sys.exit(0)
    events.write_text("1\n", encoding="utf-8"); commit(1)
    sys.exit(7)
out.write_text("ok", encoding="utf-8"); done(out)
'''


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config" / "orchestrator.json"
        self.config_path.parent.mkdir(parents=True)
        self.worker = self.root / "worker.py"
        self.worker.write_text(WORKER, encoding="utf-8")
        orchestrator.CONTROL_POLL_SECONDS = 0.05

    def tearDown(self):
        self.temp.cleanup()

    def config(self, mode="success", expensive=False):
        inherited = [key for key in ("PATH", "SystemRoot") if key in os.environ]
        contract = ({"mode": "checkpoint", "checkpoint_path": "artifacts/checkpoint.txt",
                     "receipt_path": "artifacts/checkpoint.json", "event_log_path": "artifacts/events.txt",
                     "resume_args": ["--resume-from", "{checkpoint}"],
                     "idempotent": True} if expensive else {"mode": "restart_idempotent"})
        return {
            "schema_version": 2,
            "experiment_id": "test",
            "experiment_root": "..",
            "run_root": "runs/test",
            "emergency_dir": "emergency",
            "control_reserve_bytes": 65536,
            "heartbeat_seconds": 20,
            "storage_guard": {"observation_file": "runs/control/storage_observation.json",
                "required_source": "modelscope_ui_top_right", "expected_capacity_gib": 100,
                "max_age_seconds": 45, "warn_used_gib": 90, "stop_used_gib": 95},
            "immutable_inputs": [{"id": "worker", "path": "worker.py",
                "sha256": hashlib.sha256(self.worker.read_bytes()).hexdigest()}],
            "frozen_environment": {"inherit": inherited, "set": {"PYTHONIOENCODING": "utf-8"}},
            "stages": [{"id": "smoke", "command": [sys.executable, str(self.worker), "--root", str(self.root), "--mode", mode],
                "budget_seconds": 10, "expensive": expensive, "cwd": ".",
                "max_additional_gib": 1.0 if expensive else 0.01, "resume_contract": contract,
                "success_receipt": "artifacts/success.json",
                "required_outputs": [{"id": "result", "path": "artifacts/output.txt"}],
                "progress_file": "artifacts/progress.json"}]
        }

    def write_config(self, value):
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def storage_path(self):
        return self.root / "runs" / "control" / "storage_observation.json"

    def fresh_storage(self, used=88.81, capacity=100):
        return orchestrator.write_storage_observation(self.storage_path(), used_gib=used, capacity_gib=capacity,
                                                       source="modelscope_ui_top_right")

    def run_config(self, value, **kwargs):
        self.write_config(value)
        return orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=False, **kwargs)

    def state(self):
        return json.loads((self.root / "runs/test/stages/smoke/state.json").read_text(encoding="utf-8"))

    def test_config_requires_short_ttl_and_checkpoint_for_expensive(self):
        value = self.config(expensive=True)
        orchestrator.validate_config(value)
        value["storage_guard"]["max_age_seconds"] = 900
        with self.assertRaises(orchestrator.ConfigError): orchestrator.validate_config(value)
        value = self.config(expensive=True)
        value["stages"][0]["resume_contract"] = {"mode": "restart_idempotent"}
        with self.assertRaises(orchestrator.ConfigError): orchestrator.validate_config(value)

    def test_storage_capacity_finite_fresh_and_reserved(self):
        value = self.config(expensive=True)
        guard, stage = value["storage_guard"], value["stages"][0]
        self.fresh_storage(88)
        observed = orchestrator.read_storage_observation(self.storage_path(), guard)
        envelope = orchestrator.storage_admission(observed, guard, stage)
        self.fresh_storage(94.1)
        with self.assertRaises(orchestrator.StorageGuardError):
            orchestrator.storage_admission(orchestrator.read_storage_observation(self.storage_path(), guard), guard, stage)
        self.fresh_storage(80, 99)
        with self.assertRaises(orchestrator.StorageGuardError):
            orchestrator.read_storage_observation(self.storage_path(), guard)
        with self.assertRaises(orchestrator.StorageGuardError):
            orchestrator.write_storage_observation(self.storage_path(), used_gib=float("nan"), capacity_gib=100,
                                                    source="modelscope_ui_top_right")

        self.fresh_storage(80)
        stage["max_additional_gib"] = 14
        envelope = orchestrator.storage_admission(
            orchestrator.read_storage_observation(self.storage_path(), guard), guard, stage)
        self.fresh_storage(81)
        orchestrator.storage_admission(
            orchestrator.read_storage_observation(self.storage_path(), guard), guard, stage, envelope)
        self.fresh_storage(94.1)
        with self.assertRaises(orchestrator.StorageGuardError):
            orchestrator.storage_admission(
                orchestrator.read_storage_observation(self.storage_path(), guard), guard, stage, envelope)

    def test_exit_zero_requires_outputs_and_matching_receipt(self):
        self.fresh_storage()
        value = self.config("missing")
        self.assertEqual(self.run_config(value), 1)
        self.assertEqual(self.state()["status"], "FAILED")
        self.assertIn("success receipt", self.state()["failure_reason"])
        self.fresh_storage()
        value = self.config("mismatch")
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=False, restart=True), 1)
        self.assertIn("output mismatch", self.state()["failure_reason"])

    def test_done_resume_revalidates_inputs_and_outputs(self):
        self.fresh_storage()
        value = self.config()
        self.assertEqual(self.run_config(value), 0)
        done = self.state()
        self.assertEqual(done["status"], "DONE")
        self.assertTrue(done["output_evidence"]["outputs"])
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 0)
        (self.root / "artifacts/output.txt").write_text("tampered", encoding="utf-8")
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertEqual(self.state()["status"], "DONE")  # good receipt is preserved for diagnosis

    def test_done_joint_output_and_worker_receipt_replacement_is_rejected(self):
        self.fresh_storage()
        value = self.config()
        self.assertEqual(self.run_config(value), 0)
        output = self.root / "artifacts/output.txt"
        receipt_path = self.root / "artifacts/success.json"
        output.write_text("replacement", encoding="utf-8")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["outputs"]["result"] = {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                                         "size_bytes": output.stat().st_size}
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertEqual(self.state()["status"], "DONE")

    def test_checkpoint_resume_continues_exactly_once(self):
        self.fresh_storage()
        value = self.config("checkpoint", expensive=True)
        self.assertEqual(self.run_config(value), 1)
        self.assertEqual((self.root / "artifacts/events.txt").read_text().splitlines(), ["1"])
        self.fresh_storage()
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 0)
        self.assertEqual((self.root / "artifacts/events.txt").read_text().splitlines(), ["1", "2"])
        self.assertEqual(self.state()["resume_from"]["last_committed_unit"], 1)

    def test_checkpoint_rollback_and_same_unit_fork_are_rejected(self):
        self.fresh_storage()
        value = self.config("checkpoint", expensive=True)
        self.assertEqual(self.run_config(value), 1)
        receipt_path = self.root / "artifacts/checkpoint.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["last_committed_unit"] = 0
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.fresh_storage()
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertIn("rollback rejected", self.state()["failure_reason"])

        # Restore the sealed unit, then fork the checkpoint contents at the same unit.
        checkpoint = self.root / "artifacts/checkpoint.txt"
        checkpoint.write_text("fork", encoding="utf-8")
        receipt["last_committed_unit"] = 1
        receipt["checkpoint_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.fresh_storage()
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertIn("fork rejected", self.state()["failure_reason"])

    def test_checkpoint_event_boundary_must_match_receipt(self):
        self.fresh_storage()
        value = self.config("checkpoint", expensive=True)
        self.assertEqual(self.run_config(value), 1)
        with (self.root / "artifacts/events.txt").open("a", encoding="utf-8") as handle:
            handle.write("uncommitted\n")
        self.fresh_storage()
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertIn("committed boundary", self.state()["failure_reason"])

    def test_process_tree_is_terminated(self):
        marker = self.root / "grandchild-marker"
        grandchild = self.root / "grandchild.py"
        grandchild.write_text("import signal,time,sys\n"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN) if hasattr(signal,'SIGTERM') else None\n"
                              "time.sleep(1.2)\nopen(sys.argv[1],'w').write('survived')\n", encoding="utf-8")
        leader = self.root / "leader.py"
        leader.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
                          "print('spawned',flush=True)\ntime.sleep(30)\n", encoding="utf-8")
        managed = orchestrator.start_managed_process([sys.executable, str(leader), str(grandchild), str(marker)],
                                                     cwd=self.root, env=os.environ)
        time.sleep(0.25)
        managed.stop_tree(grace_seconds=0.2)
        if managed.process.stdout is not None:
            managed.process.stdout.close()
        time.sleep(1.4)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object stress test")
    def test_windows_suspended_assignment_fast_launcher_stress(self):
        markers = []
        grandchild = self.root / "fast-grandchild.py"
        grandchild.write_text("import sys,time\ntime.sleep(.8)\nopen(sys.argv[1],'w').write('escaped')\n", encoding="utf-8")
        leader = self.root / "fast-leader.py"
        leader.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
                          "open(sys.argv[3],'w').write('ready')\ntime.sleep(30)\n", encoding="utf-8")
        for index in range(12):
            marker = self.root / f"escape-{index}"
            ready = self.root / f"ready-{index}"
            markers.append(marker)
            managed = orchestrator.start_managed_process(
                [sys.executable, str(leader), str(grandchild), str(marker), str(ready)],
                cwd=self.root, env=os.environ)
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            managed.stop_tree(grace_seconds=0.1)
            if managed.process.stdout is not None:
                managed.process.stdout.close()
        time.sleep(1)
        self.assertFalse(any(marker.exists() for marker in markers))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object abrupt-owner test")
    def test_windows_abrupt_owner_death_kills_grandchild(self):
        marker = self.root / "abrupt-grandchild"
        ready = self.root / "abrupt-ready"
        grandchild = self.root / "abrupt-grandchild.py"
        grandchild.write_text("import sys,time\ntime.sleep(1)\nopen(sys.argv[1],'w').write('escaped')\n", encoding="utf-8")
        leader = self.root / "abrupt-leader.py"
        leader.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
                          "open(sys.argv[3],'w').write('ready')\ntime.sleep(30)\n", encoding="utf-8")
        helper = self.root / "job-owner.py"
        helper.write_text(
            "import importlib.util,os,sys,time\n"
            "spec=importlib.util.spec_from_file_location('orch',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "managed=m.start_managed_process([sys.executable,sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]],cwd=__import__('pathlib').Path(sys.argv[6]),env=os.environ)\n"
            "deadline=time.monotonic()+3\n"
            "while not __import__('pathlib').Path(sys.argv[5]).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "os._exit(0)\n", encoding="utf-8")
        subprocess = __import__("subprocess")
        subprocess.run([sys.executable, str(helper), str(MODULE_PATH), str(leader), str(grandchild),
                        str(marker), str(ready), str(self.root)], check=True, timeout=10)
        self.assertTrue(ready.exists())
        time.sleep(1.3)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "PR_SET_PDEATHSIG is Linux-specific")
    def test_linux_parent_death_kills_leader_and_spawned_grandchild(self):
        marker = self.root / "parent-death-grandchild-marker"
        ready = self.root / "grandchild-spawned"
        grandchild = self.root / "delayed-grandchild.py"
        grandchild.write_text("import signal,sys,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                              "time.sleep(1)\nopen(sys.argv[1],'w').write('survived')\n", encoding="utf-8")
        leader = self.root / "spawning-leader.py"
        leader.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
                          "open(sys.argv[3],'w').write('ready')\ntime.sleep(30)\n", encoding="utf-8")
        intermediary = os.fork()
        if intermediary == 0:
            managed = orchestrator.start_managed_process([sys.executable, str(leader), str(grandchild),
                                                           str(marker), str(ready)],
                                                          cwd=self.root, env=os.environ)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            os._exit(0)
        os.waitpid(intermediary, 0)
        self.assertTrue(ready.exists())
        time.sleep(1.3)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX signal lifecycle test")
    def test_sigterm_persists_failed_and_releases_lock(self):
        import threading
        self.fresh_storage()
        value = self.config("sleep")
        timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
        import signal
        timer.start()
        try:
            self.assertEqual(self.run_config(value), 1)
        finally:
            timer.cancel()
        self.assertIn("supervisor_signal:SIGTERM", self.state()["failure_reason"])
        self.assertFalse((self.root / "runs/test/stages/smoke/.lock").exists())

    def test_stale_lock_recovery_requires_no_live_owner_or_group(self):
        lock_dir = self.root / "stage/.lock"
        lock_dir.mkdir(parents=True)
        orchestrator.atomic_json(lock_dir / "lease.json", {"hostname": "dead-host", "boot_id": "old",
            "owner_pid": 99999999, "owner_identity": "dead", "child_pgid": None})
        lease = orchestrator.StageLease(lock_dir, "smoke")
        lease.acquire()
        lease.release()
        self.assertFalse(lock_dir.exists())
        self.assertTrue(list(lock_dir.parent.glob(".lock.recovered-*")))

    def test_live_lock_cannot_be_stolen(self):
        lock_dir = self.root / "live/.lock"
        first = orchestrator.StageLease(lock_dir, "smoke")
        first.acquire()
        try:
            with self.assertRaises(orchestrator.LockError):
                orchestrator.StageLease(lock_dir, "smoke").acquire()
        finally:
            first.release()

    def test_enospc_finalization_writes_emergency_and_releases_lock(self):
        self.fresh_storage()
        value = self.config()
        real_append = orchestrator.append_jsonl
        calls = {"n": 0}
        def injected(path, record):
            calls["n"] += 1
            raise OSError(errno.ENOSPC, "fault injected")
        with mock.patch.object(orchestrator, "append_jsonl", side_effect=injected):
            self.assertEqual(self.run_config(value), 1)
        self.assertFalse((self.root / "runs/test/stages/smoke/.lock").exists())
        self.assertEqual(self.state()["status"], "FAILED")
        emergencies = list((self.root / "emergency").glob("*.emergency.json"))
        self.assertEqual(len(emergencies), 1)
        self.assertIn("fault injected", emergencies[0].read_text(encoding="utf-8"))

    def test_enospc_state_replacement_makes_emergency_authoritative_and_blocks_resume(self):
        self.fresh_storage()
        value = self.config()
        self.write_config(value)
        stage_dir = self.root / "runs/test/stages/smoke"
        stage_dir.mkdir(parents=True)
        state_path = stage_dir / "state.json"
        attempts_path = stage_dir / "attempts.jsonl"
        reserve = stage_dir / ".control-reserve"
        reserve.write_bytes(b"reserved")
        running = {"schema_version": 2, "experiment_id": "test", "stage": "smoke", "attempt": 1,
                   "status": "RUNNING"}
        state_path.write_text(json.dumps(running), encoding="utf-8")
        terminal = {**running, "status": "DONE", "failure_reason": None}
        real_atomic = orchestrator.atomic_json
        def injected(path, payload):
            if Path(path) == state_path:
                raise OSError(errno.ENOSPC, "state replacement fault")
            return real_atomic(path, payload)
        with mock.patch.object(orchestrator, "atomic_json", side_effect=injected):
            errors = orchestrator.finalize_record(terminal, state_path, attempts_path, reserve, self.root / "emergency")
        self.assertTrue(errors)
        self.assertEqual(json.loads(state_path.read_text())["status"], "RUNNING")
        self.assertFalse(attempts_path.exists())
        emergency = orchestrator.latest_authoritative_emergency(self.root / "emergency", "test", "smoke")
        self.assertIsNotNone(emergency)
        self.assertEqual(emergency["record"]["status"], "FAILED")
        self.assertIn("state replacement fault", emergency["record"]["failure_reason"])
        self.assertEqual(orchestrator.run_stage(self.config_path, value, value["stages"][0], resume=True), 1)
        self.assertFalse((stage_dir / ".lock").exists())

    def test_reserve_release_failure_never_commits_done(self):
        state = self.root / "state.json"
        attempts = self.root / "attempts.jsonl"
        reserve_directory = self.root / "reserve-as-directory"
        reserve_directory.mkdir()
        record = {"experiment_id": "test", "stage": "smoke", "attempt": 2,
                  "status": "DONE", "failure_reason": None}
        errors = orchestrator.finalize_record(record, state, attempts, reserve_directory, self.root / "emergency")
        self.assertTrue(errors)
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "FAILED")
        self.assertEqual(json.loads(attempts.read_text(encoding="utf-8").splitlines()[0])["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()

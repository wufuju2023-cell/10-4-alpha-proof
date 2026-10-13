#!/usr/bin/env bash
# Build the ephemeral signing identity and immutable public pins needed by
# run_wave_join_adapter.py after a ModelScope instance restart.
set -euo pipefail

EXP_ROOT="${EXP_ROOT:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}"
RUNTIME_PROJECT="${RUNTIME_PROJECT:-/tmp/fate-m-reap428/runtime}"
RUNTIME_RECEIPT="${RUNTIME_RECEIPT:-/tmp/fate-m-reap428/runtime_receipt.json}"
LAKE="${LAKE:-$EXP_ROOT/runtime/toolchains/lean-4.28.0-linux/bin/lake}"
PRIVATE_KEY="${PRIVATE_KEY:-/tmp/fate-verifier-ed25519.key}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"

STRICT_REPLAY="$EXP_ROOT/workstreams/lean_integration/src/fate_reap/strict_replay.py"
COURSE_MANIFEST="$RUNTIME_PROJECT/lake-manifest.json"
PROTOCOL_CONFIG="$EXP_ROOT/config/subset_20x10_protocol.frozen.json"

for required in "$RUNTIME_RECEIPT" "$LAKE" "$STRICT_REPLAY" \
                "$COURSE_MANIFEST" "$RUNTIME_PROJECT/lean-toolchain" "$PROTOCOL_CONFIG"; do
  if [[ ! -f "$required" ]]; then
    printf 'bootstrap error: required file is missing: %s\n' "$required" >&2
    exit 2
  fi
done
if [[ ! -x "$LAKE" ]]; then
  printf 'bootstrap error: lake is not executable: %s\n' "$LAKE" >&2
  exit 2
fi
case "$(realpath -m "$PRIVATE_KEY")" in
  /tmp/*|/run/secrets/*) ;;
  *)
    printf 'bootstrap error: PRIVATE_KEY must be below /tmp or /run/secrets\n' >&2
    exit 2
    ;;
esac

umask 077
mkdir -p "$(dirname "$PRIVATE_KEY")"

# Re-running on the same live instance reuses the same key.  A restart removes
# /tmp and therefore creates a new identity and a new content-addressed pin dir.
PRIVATE_KEY="$PRIVATE_KEY" python3 - <<'PY'
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

path = Path(os.environ["PRIVATE_KEY"])
if not path.exists():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
os.chmod(path, 0o600)
raw = path.read_bytes()
if len(raw) != 32:
    raise SystemExit("bootstrap error: verifier key is not raw Ed25519 (32 bytes)")
Ed25519PrivateKey.from_private_bytes(raw)
PY

PUBLIC_KEY_HEX="$(PRIVATE_KEY="$PRIVATE_KEY" python3 - <<'PY'
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.from_private_bytes(Path(os.environ["PRIVATE_KEY"]).read_bytes())
print(key.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
PY
)"
RUNTIME_SHA="$(sha256sum "$RUNTIME_RECEIPT" | awk '{print $1}')"
MANIFEST_SHA="$(sha256sum "$COURSE_MANIFEST" | awk '{print $1}')"
LAKE_SHA="$(sha256sum "$LAKE" | awk '{print $1}')"
EXECUTOR_SHA="$(sha256sum "$STRICT_REPLAY" | awk '{print $1}')"
LEAN_VERSION="$(cd "$RUNTIME_PROJECT" && "$LAKE" env lean --version)"
LEAN_GIT_HASH="$(cd "$RUNTIME_PROJECT" && "$LAKE" env lean -g)"
PIN_ROOT="${PIN_ROOT:-$EXP_ROOT/runs/formal/verifier-bootstrap-${RUNTIME_SHA:0:12}-${PUBLIC_KEY_HEX:0:12}}"
mkdir -p "$PIN_ROOT"

export EXP_ROOT RUNTIME_PROJECT RUNTIME_RECEIPT LAKE PRIVATE_KEY TIMEOUT_SECONDS
export STRICT_REPLAY COURSE_MANIFEST PROTOCOL_CONFIG PIN_ROOT PUBLIC_KEY_HEX
export RUNTIME_SHA MANIFEST_SHA LAKE_SHA EXECUTOR_SHA LEAN_VERSION LEAN_GIT_HASH
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

def load(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)

def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")

def publish_identical(path, value):
    path = Path(path)
    payload = canonical_bytes(value)
    if path.exists():
        if path.read_bytes() != payload:
            raise SystemExit(f"bootstrap error: refusing to replace differing pin: {path}")
        return
    pending = path.with_name(path.name + ".pending")
    try:
        with pending.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)

runtime = load(os.environ["RUNTIME_RECEIPT"])
manifest = load(os.environ["COURSE_MANIFEST"])
if runtime.get("schema_version") != "fate.reap.runtime.v1":
    raise SystemExit("bootstrap error: unsupported runtime receipt")
if runtime.get("runtime_lake_manifest_sha256") != os.environ["MANIFEST_SHA"]:
    raise SystemExit("bootstrap error: runtime receipt/course manifest mismatch")
if runtime.get("lean_version") != os.environ["LEAN_VERSION"]:
    raise SystemExit("bootstrap error: live Lean version differs from runtime receipt")
if runtime.get("lean_githash") != os.environ["LEAN_GIT_HASH"]:
    raise SystemExit("bootstrap error: live Lean git hash differs from runtime receipt")
if (runtime.get("lake_executable_sha256") is not None and
        runtime["lake_executable_sha256"] != os.environ["LAKE_SHA"]):
    raise SystemExit("bootstrap error: lake hash differs from runtime receipt")
if Path(os.environ["RUNTIME_PROJECT"], "lean-toolchain").read_text(
        encoding="utf-8").strip() != "leanprover/lean4:v4.28.0":
    raise SystemExit("bootstrap error: course project is not Lean 4.28.0")

packages = manifest.get("packages")
if not isinstance(packages, list):
    raise SystemExit("bootstrap error: lake manifest has no packages")
try:
    mathlib_revision = next(
        package["rev"] for package in packages
        if isinstance(package, dict) and package.get("name") == "mathlib"
    )
except (StopIteration, KeyError):
    raise SystemExit("bootstrap error: lake manifest has no pinned mathlib revision")
if not isinstance(mathlib_revision, str) or len(mathlib_revision) != 40:
    raise SystemExit("bootstrap error: invalid mathlib revision")

protocol = load(os.environ["PROTOCOL_CONFIG"])
protocol_budget = protocol.get("budget", {})
if (protocol_budget.get("max_attempts_per_task") != 4 or
        protocol_budget.get("max_new_tokens_per_attempt") != 512):
    raise SystemExit("bootstrap error: frozen protocol is not the formal 4x512 contract")

pin_root = Path(os.environ["PIN_ROOT"])
budget = {
    "schema_version": "fate.shared_budget.formal_wave.v1",
    "max_attempts_per_problem": 4,
    "max_generated_tokens_per_problem": 4 * 64 * 512,
    "max_lean_tactic_executions_per_problem": 4 * 64,
    "generation": {"num_return_sequences": 64, "max_new_tokens": 512},
    "stopping": "first_strict_success_or_four_attempts",
}
publish_identical(pin_root / "shared_budget.formal_wave.json", budget)

verifier_id = ("lean428-kernel-" + os.environ["RUNTIME_SHA"][:12] + "-" +
               os.environ["PUBLIC_KEY_HEX"][:12])
lock = {
    "schema_version": 1,
    "verifier_id": verifier_id,
    "ed25519_public_key_hex": os.environ["PUBLIC_KEY_HEX"],
    "lean_toolchain": "leanprover/lean4:v4.28.0",
    "lean_version": os.environ["LEAN_VERSION"],
    "lean_git_hash": os.environ["LEAN_GIT_HASH"],
    "mathlib_revision": mathlib_revision,
    "executor_source_sha256": os.environ["EXECUTOR_SHA"],
    "executor_binary_sha256": os.environ["LAKE_SHA"],
    "runtime_receipt_sha256": os.environ["RUNTIME_SHA"],
    "lake_manifest_sha256": os.environ["MANIFEST_SHA"],
    "kernel_command": ["{lake}", "env", "lean", "--json", "-E", "hasSorry", "{theorem}"],
    "tactic_timeout_seconds": float(os.environ["TIMEOUT_SECONDS"]),
    "forbidden_tokens": ["sorry", "admit", "holes"],
}
publish_identical(pin_root / "trusted_verifier_lock.json", lock)

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

summary = {
    "schema_version": "fate.formal_verifier_bootstrap.v1",
    "pin_root": str(pin_root.resolve()),
    "private_key_path": str(Path(os.environ["PRIVATE_KEY"]).resolve()),
    "private_key_persistent": False,
    "course_project": str(Path(os.environ["RUNTIME_PROJECT"]).resolve()),
    "course_manifest": str(Path(os.environ["COURSE_MANIFEST"]).resolve()),
    "course_manifest_sha256": os.environ["MANIFEST_SHA"],
    "runtime_receipt": str(Path(os.environ["RUNTIME_RECEIPT"]).resolve()),
    "runtime_receipt_sha256": os.environ["RUNTIME_SHA"],
    "lake": str(Path(os.environ["LAKE"]).resolve()),
    "lake_sha256": os.environ["LAKE_SHA"],
    "budget_config": str((pin_root / "shared_budget.formal_wave.json").resolve()),
    "budget_config_sha256": sha256(pin_root / "shared_budget.formal_wave.json"),
    "verifier_lock": str((pin_root / "trusted_verifier_lock.json").resolve()),
    "verifier_lock_sha256": sha256(pin_root / "trusted_verifier_lock.json"),
    "verifier_id": verifier_id,
}
publish_identical(pin_root / "bootstrap.json", summary)
print(json.dumps(summary, sort_keys=True))
PY

printf 'BOOTSTRAP_JSON=%s/bootstrap.json\n' "$PIN_ROOT"
printf 'PRIVATE_KEY=%s\n' "$PRIVATE_KEY"

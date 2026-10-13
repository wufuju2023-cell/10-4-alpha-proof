module

public meta import Reap.Tactic.TreeSearch

open Lean Elab Tactic

public meta section

namespace Reap.Training

private def requiredNat (value label : String) : IO Nat := do
  match value.toNat? with
  | some n => return n
  | none => throw <| IO.userError s!"{label} must be a nonnegative integer"

private def field {α : Type} [FromJson α] (record : Json) (name : String) : IO α := do
  match record.getObjValAs? α name with
  | .ok value => return value
  | .error error => throw <| IO.userError s!"Invalid checkpoint ACK {name}: {error}"

private def paddedStep (step : Nat) : String :=
  let text := toString step
  String.ofList (List.replicate (6 - text.length) '0') ++ text

private def appendEvent (path : System.FilePath) (record : Json) : IO Unit :=
  IO.FS.withFile path .append fun handle => do
    handle.putStrLn record.compress
    handle.flush

private def isLowerHexSha256 (value : String) : Bool :=
  value.length == 64 && value.toList.all fun c =>
    ('0' ≤ c && c ≤ '9') || ('a' ≤ c && c ≤ 'f')

/-- Hash UTF-8 bytes with the pinned runtime's sha256sum.  Missing/broken
hash tooling is an infrastructure error: observer mode never emits a fake or
non-cryptographic state identity. -/
private def sha256Text (directory : System.FilePath) (sequence : Nat)
    (label payload : String) : IO String := do
  let input := directory / s!".observer-sha256-{sequence}-{label}.tmp"
  if ← input.pathExists then
    throw <| IO.userError s!"Refusing pre-existing observer hash input {input}"
  try
    IO.FS.writeFile input payload
    let command := (← IO.getEnv "REAP_SHA256_COMMAND").getD "sha256sum"
    let output ← IO.Process.output { cmd := command, args := #[input.toString] }
    unless output.exitCode == 0 do
      throw <| IO.userError s!"{command} failed while hashing observer payload"
    let digest := (output.stdout.splitOn " ").head?.getD ""
    unless isLowerHexSha256 digest do
      throw <| IO.userError s!"{command} returned a non-canonical SHA-256"
    return digest
  finally
    if ← input.pathExists then IO.FS.removeFile input

private def enrichCanonicalEvent (directory : System.FilePath) (sequence : Nat)
    (trajectoryId : String) (record : Json) : IO Json := do
  let kind := (record.getObjValAs? String "kind").toOption.getD ""
  let record := if kind == "canonical_candidate" || kind == "canonical_candidate_result" then
    record.setObjVal! "trajectory_id" (toJson trajectoryId)
  else record
  if kind == "canonical_candidate" then
    let before : String ← field record "state_before_payload"
    return record.setObjVal! "state_before_sha256"
      (toJson (← sha256Text directory sequence "state-before" before))
  else if kind == "canonical_candidate_result" then
    let before : String ← field record "state_before_payload"
    let after : String ← field record "state_after_payload"
    let enriched := record
      |>.setObjVal! "state_before_sha256"
        (toJson (← sha256Text directory sequence "state-before" before))
      |>.setObjVal! "state_after_sha256"
        (toJson (← sha256Text directory sequence "state-after" after))
    let receiptPayload := enriched.compress
    let receiptHash ← sha256Text directory sequence "executor-receipt" receiptPayload
    return enriched
      |>.setObjVal! "executor_receipt_payload" (toJson receiptPayload)
      |>.setObjVal! "executor_receipt_sha256" (toJson receiptHash)
  else if kind == "canonical_selected_path" then
    let script : String ← field record "proof_script"
    let enriched := record.setObjVal! "proof_script_sha256"
      (toJson (← sha256Text directory sequence "proof-script" script))
    let receiptPayload := enriched.compress
    let receiptHash ← sha256Text directory sequence "selected-path-receipt" receiptPayload
    return enriched
      |>.setObjVal! "selected_path_receipt_payload" (toJson receiptPayload)
      |>.setObjVal! "selected_path_receipt_sha256" (toJson receiptHash)
  else
    return record

/-- Wait only at a completed iteration. The caller must publish ACK files atomically.
No timeout or malformed/error ACK permits the search to continue. -/
private def awaitCheckpoint (directory : System.FilePath) (sessionId treeId : String)
    (step version timeoutSeconds : Nat) : IO Nat := do
  let path := directory / s!"checkpoint-{paddedStep step}.ack.json"
  let deadline := (← IO.monoMsNow) + timeoutSeconds * 1000
  while !(← path.pathExists) do
    if (← IO.monoMsNow) >= deadline then
      throw <| IO.userError s!"Checkpoint {step} ACK timed out (barrier protection, not total TTT deadline)"
    IO.sleep 20
  let record ← match Json.parse (← IO.FS.readFile path) with
    | .ok record => pure record
    | .error error => throw <| IO.userError s!"Invalid checkpoint ACK JSON: {error}"
  let ackSession : String ← field record "session_id"
  let ackTree : String ← field record "tree_id"
  let ackStep : Nat ← field record "step"
  let previousVersion : Nat ← field record "previous_policy_version"
  let status : String ← field record "status"
  let nextVersion : Nat ← field record "policy_version"
  unless ackSession == sessionId && ackTree == treeId && ackStep == step do
    throw <| IO.userError "Checkpoint ACK session/tree/step mismatch"
  unless previousVersion == version do
    throw <| IO.userError "Checkpoint ACK previous_policy_version mismatch"
  unless nextVersion > version do
    throw <| IO.userError "Checkpoint ACK must advance policy_version"
  if status == "error" then
    let message := (record.getObjValAs? String "error").toOption.getD "coordinator reported an error"
    throw <| IO.userError s!"Checkpoint coordinator failed: {message}"
  unless status == "continue" do
    throw <| IO.userError "Checkpoint ACK status must be continue or error"
  return nextVersion

/-- Default-off stream. Only JSON leaves the core; checkpoint ACK changes the
external policy version, never Lean goals or the in-memory MCTS tree. -/
def makeTrainingObserver (sessionId : String) : IO (Option Reap.TreeSearch.MCTSObserver) := do
  let pathText := (← IO.getEnv "REAP_OBSERVER_PATH").getD ""
  if pathText.isEmpty then
    return none
  let path := System.FilePath.mk pathText
  if let some parent := path.parent then IO.FS.createDirAll parent
  let treeId := (← IO.getEnv "REAP_TREE_ID").getD sessionId
  let initialVersion ← requiredNat ((← IO.getEnv "REAP_POLICY_VERSION").getD "0") "REAP_POLICY_VERSION"
  let timeout ← requiredNat ((← IO.getEnv "REAP_CHECKPOINT_TIMEOUT_SECONDS").getD "900") "REAP_CHECKPOINT_TIMEOUT_SECONDS"
  if timeout == 0 then throw <| IO.userError "Checkpoint timeout must be positive"
  let checkpointDir := (← IO.getEnv "REAP_CHECKPOINT_DIR").filter (!·.isEmpty)
  if let some directory := checkpointDir then IO.FS.createDirAll (.mk directory)
  let sequence ← IO.mkRef (0 : Nat)
  let policyVersion ← IO.mkRef initialVersion
  let emit (record : Json) : IO Unit := do
    let index ← sequence.modifyGet fun n => (n, n + 1)
    let version ← policyVersion.get
    let parent := path.parent.getD (System.FilePath.mk ".")
    let wrapped := record
      |>.setObjVal! "schema_version" (toJson "reap.training.observer.v1")
      |>.setObjVal! "session_id" (toJson sessionId)
      |>.setObjVal! "tree_id" (toJson treeId)
      |>.setObjVal! "policy_version" (toJson version)
      |>.setObjVal! "sequence" (toJson index)
      |>.setObjVal! "monotonic_ns" (toJson (← IO.monoNanosNow))
    let enriched ← enrichCanonicalEvent parent index s!"trajectory:{sessionId}:{treeId}" wrapped
    appendEvent path enriched
  return some fun record => do
    liftM <| emit record
    if (record.getObjValAs? String "kind").toOption == some "checkpoint" then
      if let some directory := checkpointDir then
        let step : Nat ← liftM <| field record "step"
        let previousVersion ← policyVersion.get
        let nextVersion ← liftM <| awaitCheckpoint (.mk directory) sessionId treeId step
          (← policyVersion.get) timeout
        policyVersion.set nextVersion
        liftM <| emit <| json%{
          kind: "checkpoint_ack", step: $step,
          previous_policy_version: $previousVersion,
          next_policy_version: $nextVersion
        }

end Reap.Training

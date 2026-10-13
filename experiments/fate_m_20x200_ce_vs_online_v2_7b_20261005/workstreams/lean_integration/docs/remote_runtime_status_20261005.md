# Remote runtime bootstrap status — 2026-10-05

This note now includes the read-only archive verification performed in the
active ModelScope instance after the original bootstrap attempts.

An earlier ModelScope bootstrap stopped in the then-current
`prepare_runtime.sh` at line 41 with:

```text
lake: command not found
```

Cause: the script invoked bare `lake` and therefore silently depended on the
launcher's `PATH`, although the experiment-owned Lean 4.28 toolchain already
has a stable location:

```text
/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runtime/toolchains/lean-4.28.0-linux/bin/lake
```

Local remediation now resolves one executable before any clone/build, in this
strict priority order:

1. explicit `LAKE_BIN`;
2. explicit `LEAN_BIN_DIR/lake`;
3. `<experiment-root>/runtime/toolchains/lean-4.28.0-linux/bin/lake`.

There is no implicit fallback to a bare `lake` from `PATH`. Missing or
non-executable candidates stop with exit code 127 and a diagnostic. Every build
stage and runtime-provenance subprocess receives the same resolved executable.

## pathfix5 result

The parent-reported `pathfix5` resume confirms that the local path fix works:

- the pinned Reap training target completed all **198 jobs**;
- `lake update` restored the pinned dependency graph and entered Mathlib's
  post-update hooks;
- the following packages were restored at their fixed commits:
  `LeanSearchClient`, `importGraph`, `proofwidgets`, `aesop`, `Qq`, `batteries`,
  `Cli`, `openAI_client`, and `requests`;
- the runtime/build tree remains on ephemeral `/tmp`, not persistent storage;
- ModelScope persistent storage remains at **88.6%**.

The former `lake: command not found` condition is therefore resolved and is no
longer the active blocker.

## Current blocker and recovered asset

The only reported blocker is now the network transfer inside:

```text
lake exe cache get
```

While downloading official `leantar` **0.1.16** from the
`digama0/leangz` GitHub release URL, curl stopped with:

```text
curl: (56) unexpected EOF
```

This was a partial release-asset download failure, not evidence of a Lean,
Mathlib, Reap, dependency-pin, or disk-capacity failure. The archive has since
been downloaded successfully to:

```text
/root/.cache/mathlib/leantar-0.1.16.tar.gz
```

Read-only verification on 2026-10-05 established:

- gzip/tar listing succeeds (`TAR_LIST_RC=0`);
- archive size is **2,362,400 bytes**;
- archive SHA-256 is
  `2cbc40ca214227a0e536721c021bad719a802dd86b1f2d8989571efabfbdd3d1`;
- the archive contains
  `leantar-v0.1.16-x86_64-unknown-linux-musl/leantar`;
- a persistent checksum receipt was written to
  `/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/runs/preflight/lean-bootstrap/derived/leantar_archive.sha256`.

The remaining gate is no longer network acquisition. It is explicit human
confirmation immediately before extracting/running this newly downloaded
official executable, followed by the existing `lake exe cache get` resume.

## Next safe action

The next remote action remains owned by the main agent:

1. retain and resume the existing `/tmp` runtime tree;
2. recheck `/tmp` free space and confirm persistent storage remains below the
   90% warning threshold;
3. after explicit confirmation, extract/use only the verified official
   `leantar` archive and retry the failed cache-fetch/resume stage;
4. do not start a second Lean/Reap checkout, redo the already successful Reap
   build, or place the runtime on persistent storage;
5. if the same asset fails repeatedly, preserve the exact URL/curl diagnostics
   and treat it as an external network blocker rather than deleting proven
   build state.

No implementation change is required for this new failure.

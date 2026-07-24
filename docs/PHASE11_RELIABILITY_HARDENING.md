# Phase 11 Reliability Hardening

This unit begins Phase 11.2 through 11.4 as one coordinated reliability track.

## 11.2 Crash-consistent publication

A shared `AtomicDirectoryPublisher` builds a complete generation beside the destination and commits it with an atomic rename. Overwrite no longer begins by deleting the last known-good generation.

The primitive distinguishes:

- failure before commit: the previous generation remains authoritative;
- failure after the new generation becomes visible: the operation reports that commit occurred but durability confirmation is uncertain;
- successful commit and parent-directory sync: the new generation is authoritative and the backup is removed.

All overwritable artifact exporters use this primitive (including remediation,
recovery observations/reports, evaluation, comparison, analytics, detection,
impact, evidence, simulator, and investigation outputs). A writer holds the
matching artifact lease for the complete transaction; readers use that same
lease root.

The publisher now serializes writers with a per-target exclusive lock. Lock files
are never broken automatically; an operator must verify the recorded writer is
dead before removing a stale lock. Staged payloads are recursively restricted to
regular files and directories, every file is flushed with `fsync`, and each
directory is flushed before publication. On Linux, existing generations switch
with `renameat2(RENAME_EXCHANGE)`, so readers never observe a missing target.
Platforms without that primitive fail closed rather than using an unsafe
two-rename fallback.

The exchange is the Linux `renameat2(2)` interface with
`RENAME_EXCHANGE` (`flags=2`), and both names must be directories on the same
filesystem. `ENOSYS`, `EINVAL`, `EXDEV`, and permission failures are surfaced as
controlled, non-committing publication errors; the live generation is untouched.
Initial publication without overwrite still uses ordinary `os.replace` and is
portable. Crash-consistent overwrite is therefore explicitly Linux-only.

Readers and publishers also coordinate through persistent per-artifact lease
and intent inodes. Every entrant takes the exclusive intent turnstile first and
retains it for the complete lease. This deliberately serializes artifact
readers: it closes the pathname-replacement race that could otherwise create a
second active lock domain while preserving a shared data-lock check and a
strict writer boundary. Thus a declared writer gates all later readers and
existing readers drain deterministically. The lock order is **intent -> verified parent directory -> data lease**;
multi-root callers sort canonical roots and acquire every intent before any
data lease. Kernel locks are released automatically when a process exits, so
abandoned intent is recoverable. Lease paths are never recreated automatically;
symlinked, replaced, non-regular, multiply-linked, or incorrectly-owned paths
fail closed.

The threat model assumes the artifact parent is owned by the current effective
user and is not group/world writable. Accidental replacement by another
application process is detected by descriptor/path identity revalidation. A
privileged process able to replace the verified parent directory is outside the
model; such a process can defeat filesystem locking itself.

If restoration fails, the complete backup is retained and its path is included in
the controlled error. Cleanup never deletes the only remaining complete generation.

## 11.3 Corruption and failure injection

Publication tests inject failures at staging creation, payload completion, old-generation movement, new-generation visibility, and parent sync. TUI corruption cases remain validator-driven and must fail closed without tracebacks.

## 11.4 Endurance

`paic.tui.hardening` repeatedly builds deterministic snapshots and records snapshot hashes, elapsed time, file-descriptor delta where supported, and garbage-collected object growth. It is intentionally dependency-free and suitable for CI and longer local certification runs.

This helper endurance measurement is distinct from full `inspect_workspace` endurance:
the latter revalidates and replays every authoritative source and is reported
separately when a certification run exercises it. Static helper timing must not be
presented as a substitute for real workspace inspection.

## Resumable authoritative soak

The long-running certification command is deliberately manual rather than a
normal pull-request gate. First prepare the smoke artifacts, then run:

```bash
make tui-smoke
make phase11-authoritative-soak \
  PHASE11_SOAK_ITERATIONS=25 \
  PHASE11_SOAK_DURATION_SECONDS=1800 \
  PHASE11_SOAK_DIR=.artifacts/phase11-authoritative-soak
```

`scripts/phase11_authoritative_soak.py` runs full `inspect_workspace` calls,
including its authoritative validation and replay paths. It atomically records
source commit, raw workspace-file hash, resolved-configuration hash, resource
baselines, and a machine-readable summary. Each completed iteration is appended
and fsynced to `iterations.jsonl`; metadata and `summary.json` are replaced
atomically. Local diagnostic runs may resume with the same commit and
configuration, but release certification is always invoked with `--fresh` and
the evidence validator rejects resumed release evidence: resource deltas must
cover one complete process/run, never just the final resumed segment. A
different commit or configuration fails closed rather than mixing results.

The default GC-object allowance is 2,048 objects after warm-up and an explicit
garbage collection. It is a deliberately generous leak-regression ceiling, not
a timing gate; callers can make it stricter with `--max-gc-delta`.

The command exits nonzero for an inspection error or missing stage,
nondeterministic snapshot hash, detected publication debris, or configured FD,
RSS, or GC growth threshold breach. It reports per-iteration duration and hash,
status counts, FD/RSS/tracemalloc/GC deltas, and publication
staging/backup/PID-lock debris. Persistent artifact-level control locks are
reported separately as diagnostic context and do not count as transactional
debris.
The `phase11-authoritative-soak.yml` workflow has two explicit modes. Pull
requests run a count-only inspection gate (25 complete inspections), while
`workflow_dispatch` runs the final endurance certification (25 inspections and
1,800 cumulative inspection seconds). Both run separately on Python 3.11 and
3.12 with uploaded results; they use no credentials or external providers.

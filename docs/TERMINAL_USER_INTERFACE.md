# Terminal Control Room

Phase 11 begins with a deliberately small, read-only terminal interface. It is an engineering and validation surface, not a second implementation of incident authority.

## Design rules

- The TUI calls the existing artifact loaders, validators, and deterministic replay functions.
- It never approves, executes, rolls back, reopens, or mutates an incident.
- A green **authoritative** result means the original source artifacts and configuration were supplied and replayed.
- A warning means the artifact is internally valid but full external provenance was not configured.
- JSON snapshots are deterministic and suitable for CI, regression fixtures, and support bundles.
- Paths are resolved below one configured workspace root. Absolute paths, escaping paths, symlinked configuration files, and unsafe artifact roots are rejected.

## Quick start

Build the normal smoke artifacts, then run:

```bash
python -m paic tui validate --workspace configs/tui/smoke.yaml
python -m paic tui snapshot --workspace configs/tui/smoke.yaml --format json
python -m paic tui run --workspace configs/tui/smoke.yaml
```

The standalone package entry point is equivalent:

```bash
paic-tui run --workspace configs/tui/smoke.yaml
```

`make tui-smoke` is the authoritative smoke build: it regenerates the remediation
execution and then regenerates and validates the recovery observations/report
against that exact execution. Do not treat a recovery report from a previous
execution generation as reusable. The validator fails closed when any receipt,
execution-manifest, incident, execution-time, or observation-manifest binding
differs.

Create a starter configuration with:

```bash
paic tui init \
  --output configs/tui/local.yaml \
  --workspace-id local-control-room \
  --display-name "Local incident control room" \
  --root-dir ../..
```

## Screen model

The overview shows nine understandable stages:

1. synthetic source data;
2. business metrics;
3. incident detection;
4. customer impact;
5. operational evidence;
6. root-cause investigation;
7. controlled remediation;
8. recovery verification;
9. safety evaluation.

Select a number for plain-language details. Refresh reruns all configured validators. Help explains the difference between artifact integrity and source-authoritative replay.

## Exit codes

- `0`: no configured stage has an error or missing artifact;
- `1`: at least one configured stage is invalid or missing;
- `2`: the workspace configuration or artifact-coordination layer is invalid or unavailable.

Lease and permission failures are reported as sanitized JSON errors on stderr and
never bypass locking or appear as authoritative health.

Warnings intentionally return `0`: they describe incomplete provenance configuration, not a corrupted artifact. CI environments that require every stage to be authoritative should additionally inspect the JSON `authoritative` fields.

## Phase 11 boundary

This is the first Phase 11 unit. Phase 11 remains in progress until the TUI has been exercised under repeated runs, corrupted artifacts, interrupted workflows, terminal resizing, non-interactive streams, long paths, and both supported Python versions. Docker belongs to Phase 12. The public web product is deferred until the TUI and containerized system have met their reliability gates.

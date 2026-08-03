# Development Roadmap

The roadmap is organised as capability milestones. Each milestone ends with working code, tests, documentation, and reviewable evidence.

| Status | Capability milestone | Main output |
|---|---|---|
| Complete | Product and evaluation contracts | Executable specifications, seed incidents, safety boundaries, and evaluation definitions |
| Complete | Synthetic commerce environment | Reproducible customers, catalogue, checkout, payments, orders, inventory, fulfilment, and operations data |
| Complete | Analytics and metric layer | Funnels, cohorts, contribution analysis, metric definitions, and data quality |
| Complete | Advanced anomaly detection | Seasonal baselines, distribution-aware tests, change points, sequential evidence, and false-discovery control |
| Complete | Churn and customer impact | Survival models, calibration diagnostics, incident exposure, causal benchmark estimates, and revenue risk |
| Complete | Operational evidence and lineage | Service health, changes, lineage, runbooks, historical incidents, and deterministic timelines |
| Complete | Governed Tool Gateway | Source binding, read-only tools, parsed SQL, authorization, limits, and hash-chained audit records |
| Complete | Probabilistic agentic investigation | Provider-neutral routing, bounded tool loop, competing hypotheses, probability ranking, abstention, replay, and evaluation |
| Complete | Approval and governed remediation | Trusted approver attestations, exact human approval, reversible simulated actions, risk policy, short-lived tokens, canonical local state lineage, and tamper-evident receipts |
| Complete | Recovery verification and reopening | Guardrail metrics, statistical recovery, regression detection, immutable local lifecycle, replay, and automatic reopening |
| Complete | Expanded evaluation and adversarial testing | Hidden benchmark, calibration, source-bound replay, model comparisons, real ablations, regression tests, and security attacks |
| Complete | Phase 11: TUI and exhaustive hardening | Read-only terminal control room, deterministic snapshots, lifecycle stress testing, failure injection, endurance, and shared artifact-publication hardening |
| Complete | Phase 12: Docker and production engineering | Reproducible images, hash-verified locks, vulnerability policy, supply-chain evidence, static artifact contract, identity/secrets policy, observability, backup/restore, deployment policy, rollback, and container resilience testing |
| Next | Phase 13: Web product and portfolio | Accessible static public dashboard over the validated bundle, host verification, video, and technical article |

## Delivery rule

A capability is complete only when its public interface works, deterministic checks pass, limitations are documented, generated artifacts are reproducible, and security-relevant failure paths have regression tests.

## Next capability

Phase 12.1 is complete: the existing governed CLI is packaged as a reproducible, non-root image and exercised through a one-shot Compose validation service under no-network, read-only runtime boundaries.

Phase 12.2 is complete: the exact-head container workflow generates a deterministic CycloneDX inventory and evidence bundle, binds it to the exact image ID and commit revision, validates its hashes, and retains it without collecting image environment variables or credentials.

Phase 12 is complete. The remaining implementation unit is Phase 13: build the accessible
read-only public presentation over the versioned bundle, verify its static host, and create
the associated portfolio material. Release evidence, deployment policy, rollback tooling,
and environment-boundary documentation are already present; no UI is included in this
foundation.

Runtime services and a database are not part of the current design because validated static hosting is sufficient. If evidence later proves otherwise, a separate read-only service boundary, migration plan, and bounded API contract are required. Containerization and evidence generation must not bypass source validation, tool policy, approval, token verification, canonical state stores, recovery authority, or evaluation authority.

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
| Planned | Recovery verification and reopening | Guardrail metrics, statistical recovery, regression detection, and automatic reopening |
| Planned | Expanded evaluation and adversarial testing | Hidden benchmark, calibration, model comparisons, ablations, regression tests, and security attacks |
| Planned | TUI and web product | Developer TUI and public live-investigation dashboard |
| Planned | Production and portfolio packaging | Docker, observability, hosted demo, deployment, video, and technical article |

## Delivery rule

A capability is complete only when its public interface works, deterministic checks pass, limitations are documented, generated artifacts are reproducible, and security-relevant failure paths have regression tests.

## Next capability

The next implementation unit adds statistical recovery verification, guardrail windows, regression detection, and automatic incident reopening. Action completion alone must never declare recovery.

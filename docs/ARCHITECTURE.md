# Architecture Contract

## Control flow

```text
Synthetic commerce events
        -> deterministic metric calculation
        -> statistical anomaly detection
        -> cohort and impact analysis
        -> Incident Commander planning
        -> controlled tool gateway
        -> evidence store and lineage graph
        -> probabilistic root-cause ranking
        -> action policy and human approval
        -> simulated remediation
        -> deterministic recovery verification
        -> evidence-backed report and evaluator
```

## Responsibility boundaries

### Deterministic components

- metric definitions and aggregation,
- anomaly tests and uncertainty estimates,
- SQL parsing and permissions,
- probability updates and calibration,
- action-policy enforcement,
- approval validation,
- remediation target checks,
- recovery decisions,
- benchmark scoring.

### LLM responsibilities

- investigation planning,
- tool selection,
- candidate hypothesis generation,
- deciding which evidence to seek next,
- interpretation of structured results,
- recommendation and report drafting.

The LLM is not the source of truth for facts, probabilities, permissions, or recovery.

## Planned component boundaries

- `simulation`: commerce entities, events, and incident injection
- `analytics`: metrics, funnels, cohorts, contribution analysis
- `detection`: seasonal, robust, sequential, and change-point methods
- `customer_impact`: churn, survival, causal studies, revenue impact
- `evidence`: logs, deployments, configuration, lineage, retrieval
- `tools`: scoped interfaces and SQL gateway
- `agent`: controlled state machine and investigation policy
- `governance`: approvals and action policies
- `remediation`: reversible synthetic actions
- `recovery`: statistical verification and reopening
- `evaluation`: ground truth, baselines, ablations, and adversarial tests
- `api`, `web`, and `tui`: product interfaces over the same core services

## Phase 0 architecture

Phase 0 implements only the executable contracts. No simulator, LLM call, database, or external service is introduced yet.

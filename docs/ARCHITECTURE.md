# Architecture

## End-to-end control flow

```text
Synthetic commerce environment
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

- commerce data generation and incident injection,
- metric definitions and aggregation,
- anomaly tests and uncertainty estimates,
- SQL parsing and permissions,
- probability updates and calibration,
- action-policy enforcement,
- approval validation,
- remediation target checks,
- recovery decisions,
- benchmark scoring.

### Language-model responsibilities

- investigation planning,
- tool selection,
- candidate hypothesis generation,
- deciding which evidence to seek next,
- interpretation of structured results,
- recommendation and report drafting.

The language model is not the source of truth for operational facts, calculated probabilities, permissions, or recovery.

## Implemented components

### Contract layer

`src/paic/contracts/` validates product, evaluation, safety, and incident specifications. The contracts are deliberately executable so later components cannot silently redefine workflow, ground truth, safety rules, or evaluation metrics.

### Synthetic commerce environment

`src/paic/simulator/` contains:

- strict simulation configuration,
- namespaced deterministic randomness,
- dimension and event generators,
- canonical Polars schemas,
- Parquet export and manifest generation,
- business profiling,
- in-memory and on-disk validation.

The generator emits an incident-free baseline. Incident injection remains a separate boundary so normal data, injected failures, and hidden evaluation truth can be tested independently.

### Command-line interface

`src/paic/cli.py` exposes contract validation, schema export, dataset generation, dataset validation, and dataset summaries through one `paic` command.

## Planned component boundaries

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

## Data artifact contract

Every exported dataset contains:

- one Parquet file per canonical table,
- the fully resolved simulation configuration,
- a manifest with logical time bounds, runtime dependency versions, row counts, schemas, relationships, byte sizes, and SHA-256 hashes,
- a success marker containing the configuration hash.

This makes each dataset self-describing and independently verifiable.

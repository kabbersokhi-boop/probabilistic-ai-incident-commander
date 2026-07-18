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

`src/paic/contracts/` validates product, evaluation, safety, and incident specifications. The contracts are executable so later components cannot silently redefine workflow, ground truth, safety rules, or evaluation metrics.

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

### Analytical metric layer

`src/paic/analytics/` turns canonical simulator tables into versioned analytical artifacts:

- fact models with explicit one-row-per-entity cardinality,
- a registry of 43 business and operational metrics,
- hourly and daily metric observations,
- overall, one-dimensional, and two-dimensional cohorts,
- checkout-funnel observations,
- adjacent-period contribution decomposition,
- source, arithmetic, range, reconciliation, funnel, and artifact-integrity checks,
- Parquet export with resolved configuration, metric catalog, runtime metadata, and cryptographic hashes.

Metric observations retain the value, numerator, denominator, sample size, and quality status. This allows anomaly detectors and investigation tools to reason from auditable statistics instead of hidden aggregation logic.

### Statistical detection layer

`src/paic/detection/` consumes validated metric observations and produces:

- no-lookahead rolling and seasonal median/MAD baselines,
- empirical beta-binomial predictive tests for proportions,
- Poisson or negative-binomial tests for counts,
- robust log-Student-t tests for currency and duration metrics,
- Benjamini-Hochberg q-values across concurrently monitored series,
- two-sided CUSUM change detection,
- sequential likelihood scores,
- deterministic alert eligibility, effect-size, support, and severity rules,
- anomaly and change-point events,
- ten-scenario benchmark evidence,
- self-validating Parquet artifacts tied to source-analytics lineage.

Benchmark perturbations are evaluator-only copies of metric observations. They do not modify the source dataset or analytical artifact.

### Customer-impact layer

`src/paic/impact/` consumes a validated source dataset and a bounded incident definition to produce:

- pre-incident customer features and interaction-derived exposure,
- Kaplan–Meier survival curves and Cox proportional-hazards coefficients,
- propensity scores, stabilized weights, matched pairs, and causal estimates,
- placebo, balance, calibration, and benchmark-recovery evidence,
- segment-level churn and revenue risk,
- immediate and forward financial impact with bootstrap uncertainty,
- source-bound Parquet artifacts with configuration and table hashes.

The synthetic potential-outcomes perturbation exists only in the impact analysis copy. Source commerce tables remain immutable.

### Command-line interface

`src/paic/cli.py` exposes:

- contract validation and schema export,
- dataset generation, validation, and summaries,
- analytical build, validation, and summaries,
- detector build, validation, benchmark, and summaries,
- customer-impact build, validation, and summaries.

The simulator and analytical layer are usable without a language model, database service, or web interface.

## Artifact contracts

### Source dataset

Every exported dataset contains:

- one Parquet file per canonical source table,
- the fully resolved simulation configuration,
- a manifest with logical time bounds, runtime dependency versions, row counts, schemas, relationships, byte sizes, and SHA-256 hashes,
- a success marker tied to the resolved configuration.

### Analytical artifact

Every exported analytical artifact contains:

- metric observations,
- funnel observations,
- contribution observations,
- data-quality results,
- the exact resolved analytics configuration,
- the versioned metric catalog,
- source-dataset identity and hashes,
- table schemas, row counts, timestamp bounds, byte sizes, and SHA-256 hashes,
- a success marker tied to the exact manifest bytes.

Both artifact types reject unsafe relative paths and can detect configuration, manifest, metadata, or table drift.

### Detection artifact

Every exported detection artifact contains scored observations, anomaly and change-point events, benchmark truth and results, detector-quality evidence, the resolved detector configuration, source-analytics hashes, runtime metadata, table schemas, row counts, timestamp bounds, file hashes, and a manifest-bound success marker.

### Customer-impact artifact

Every exported impact artifact contains customer features, survival curves, Cox coefficients, propensity scores, causal estimates, segment impact, financial impact, model metrics, quality evidence, the resolved impact configuration, source-dataset identity, table hashes, and a manifest-bound success marker.

## Planned component boundaries

- `evidence`: logs, deployments, configuration, lineage, retrieval
- `tools`: scoped interfaces and SQL gateway
- `agent`: controlled state machine and investigation policy
- `governance`: approvals and action policies
- `remediation`: reversible synthetic actions
- `recovery`: statistical verification and reopening
- `evaluation`: ground truth, baselines, ablations, and adversarial tests
- `api`, `web`, and `tui`: product interfaces over the same core services

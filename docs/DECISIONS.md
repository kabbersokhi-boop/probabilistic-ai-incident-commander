# Architecture Decisions

## ADR-0001: Statistical detection is outside the language model

**Decision:** Deterministic statistical or machine-learning code detects anomalies.
**Reason:** Detection must be reproducible, measurable, and benchmarkable.
**Status:** Accepted.

## ADR-0002: One principal agent before multi-agent expansion

**Decision:** Begin with one Incident Commander and add a critic only if evaluation proves a benefit.
**Reason:** Fewer coordination failures, lower cost, easier tracing, and clearer ablations.
**Status:** Accepted.

## ADR-0003: Root-cause probabilities are calculated externally

**Decision:** The language model may propose evidence and hypotheses but cannot manufacture numeric confidence.
**Reason:** Probabilities need explicit assumptions and calibration.
**Status:** Accepted.

## ADR-0004: Churn is part of incident impact

**Decision:** Churn and survival analysis estimate the downstream effect of incident exposure.
**Reason:** This keeps customer modelling connected to the operational product rather than becoming a separate notebook.
**Status:** Accepted.

## ADR-0005: Repository contracts are authoritative

**Decision:** Machine-readable specifications and versioned documentation are the source of truth across development tools.
**Reason:** Tool sessions do not share perfect memory, while the repository is reviewable and reproducible.
**Status:** Accepted.

## ADR-0006: Deliver capability milestones through focused changes

**Decision:** Each substantial capability receives a focused branch, tests, documentation, and review; public releases group compatible capabilities.
**Reason:** Smaller integration risk, clearer history, and easier regression diagnosis.
**Status:** Accepted.

## ADR-0007: Keep baseline generation separate from incident injection

**Decision:** The commerce simulator first produces a healthy incident-free baseline. Failure injection is implemented as a separate transformation with hidden truth.
**Reason:** This enables reliable detector false-positive testing, clean reproducibility, and strict separation between agent-visible data and evaluator-only truth.
**Status:** Accepted.

## ADR-0008: Export self-validating columnar datasets

**Decision:** Generated tables are written as Parquet alongside a resolved configuration and cryptographic manifest.
**Reason:** Columnar files support analytical workloads, while hashes and schema metadata make artifacts portable and auditable.
**Status:** Accepted.

## ADR-0009: Metrics preserve sufficient statistics

**Decision:** Ratio and mean observations retain their numerator and denominator; all observations retain sample size and quality status.
**Reason:** Downstream detection, reconciliation, uncertainty estimation, and audit must not depend on rounded dashboard values or hidden aggregation logic.
**Status:** Accepted.

## ADR-0010: Cohort contribution uses exact symmetric decomposition

**Decision:** Adjacent-period changes in selected ratio metrics are decomposed into symmetric within-cohort rate effects and population-mix effects.
**Reason:** The decomposition is order-independent, interpretable, and exactly reconstructs the overall rate change, making contribution claims testable.
**Status:** Accepted.

## ADR-0011: Analytical artifacts are self-validating

**Decision:** Analytical outputs are exported as Parquet with the resolved configuration, metric catalog, source identity, runtime metadata, cryptographic hashes, and a manifest-bound success marker.
**Reason:** Evaluation and anomaly detection require portable artifacts whose lineage and integrity can be independently verified.
**Status:** Accepted.

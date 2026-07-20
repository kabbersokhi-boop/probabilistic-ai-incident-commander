# Probabilistic AI Incident Commander

[![CI](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml/badge.svg)](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml)

**Evidence-grounded, governed agentic AI for diagnosing commerce incidents under uncertainty.**

Probabilistic AI Incident Commander is an open reference implementation of a governed agentic operations system that combines statistical anomaly detection, analytical investigation, probabilistic root-cause ranking, controlled remediation, and recovery verification.

It is designed to answer more than *“What changed?”* The implemented system determines:

- where the impact is concentrated,
- which explanations are supported or contradicted by evidence,
- how likely each root cause is,
- what customers and revenue are at risk,
- which remediation is permitted,
- and whether the business actually recovered.

```text
Detect -> Scope -> Investigate -> Test hypotheses -> Rank causes
       -> Request approval -> Remediate -> Verify recovery -> Report
```

## Why this project exists

Many AI operations demos stop at alert summarization or chart explanation. This project follows a stricter design:

- **Statistics detect anomalies.** A language model does not guess from a chart.
- **The agent investigates.** It selects controlled tools, gathers evidence, and adapts its plan.
- **Probability expresses uncertainty.** Root-cause confidence is calculated and evaluated, not invented in prose.
- **Policies govern actions.** SQL access, permissions, approvals, and remediation boundaries are enforced by ordinary software.
- **Recovery is measured.** An incident is not resolved merely because an action completed.
- **Evaluation uses hidden ground truth.** The system is judged across reproducible incidents, not one selected success.

## Who it is for

The project is intended for engineers and technical teams interested in:

- agentic and applied AI systems,
- AI evaluation and observability,
- data and analytics engineering,
- statistical anomaly detection,
- incident response and site reliability,
- commerce, payments, fulfilment, and marketplace operations,
- safe human-in-the-loop automation.

It is also a reproducible technical case study for evaluating how probabilistic models and language-model agents can cooperate inside a controlled software system.

## Current capabilities

The repository provides eleven completed governed capabilities. Phase 11 now adds the first read-only developer TUI unit for exhaustive validation and operational inspection.

### Executable product and evaluation contracts

- Machine-readable product, evaluation, safety, and incident specifications
- Five seed incidents with hidden root causes, decoy changes, competing hypotheses, expected evidence, remediation, and recovery criteria
- Strict Pydantic models and cross-contract validation
- JSON Schema export for external tooling
- Evaluation definitions covering detection, diagnosis, probability calibration, security, efficiency, and customer impact

### Deterministic synthetic commerce environment

- Reproducible generation from a fixed seed and validated YAML configuration
- Seventeen connected commerce and operational tables
- Realistic hourly traffic, weekday effects, regional differences, customer preferences, promotion and campaign attribution, checkout funnels, payment outcomes, inventory, fulfilment scans, returns, refunds, seller feeds, data pipelines, and deployments
- Canonical Polars schemas with primary-key and foreign-key definitions
- Compressed Parquet export with a resolved configuration, row counts, timestamps, runtime dependency versions, SHA-256 hashes, and a machine-readable manifest
- Deterministic validation for schemas, relationships, temporal ordering, financial reconciliation, inventory balance, healthy baseline rates, and simulation boundaries
- Dataset summaries for conversion, payment approval, gross order value, delivery, returns, new-versus-returning customers, acquisition channels, and warehouse scan latency

### Deterministic analytics and metric layer

- Forty-three versioned metrics across checkout, payments, orders, inventory, fulfilment, returns, seller feeds, pipelines, and deployments
- Hourly and daily observations with explicit values, numerators, denominators, sample sizes, and data-quality status
- Overall, one-dimensional, and two-dimensional cohorts, including region-by-device analysis
- A five-stage checkout funnel with stepwise conversion and drop-off calculations
- Exact adjacent-period contribution decomposition that separates within-cohort rate effects from population-mix effects
- Reconciliation checks that independently rebuild overall totals from cohort observations
- Self-validating Parquet artifacts with resolved configuration, metric catalog, table hashes, runtime metadata, and tamper-evident success markers
- No language-model dependency: every published analytical value is calculated by deterministic Polars code

### Statistical anomaly-detection engine

- Rolling and seasonal median/MAD baselines that use only prior observations
- Distribution-aware predictive tests: empirical beta-binomial for proportions, Poisson or negative binomial for counts, and robust log-Student-t tests for positive skewed values
- Benjamini-Hochberg false-discovery control across simultaneously monitored series
- Two-sided CUSUM change detection and sequential likelihood scoring
- Cohort-specific eligibility, sample-size, effect-size, and detector-support policies
- Auditable outputs containing expected ranges, residuals, p-values, q-values, change scores, detector support, severity, and event boundaries
- Ten deterministic ground-truth perturbations spanning checkout, payments, orders, revenue, inventory, fulfilment, pipelines, and seller feeds
- A reference standard run with 100% scenario recall, 81.25% precision, a 0.24% false-positive rate, and 1.2-period mean detection delay
- Self-validating Parquet artifacts bound to the exact source analytics manifest and detector configuration

The simulator intentionally generates a **healthy, incident-free baseline**. Detector evaluation applies evaluator-only perturbations to selected metric observations, leaving the source dataset unchanged and preserving a clean false-positive benchmark.

### Customer impact, survival, and causal analysis

- Incident-linked exposure cohorts derived from customer interactions, region, device, and operational outcome
- Pre-incident RFM, tenure, spend, discount, category, checkout, payment, fulfilment, return, and refund features
- Kaplan–Meier survival curves with confidence intervals and a regularized Cox proportional-hazards model
- Propensity-score matching, stabilized inverse-probability weighting, difference-in-differences, placebo checks, and covariate-balance diagnostics
- Bootstrap uncertainty for incremental churn and total financial impact
- Immediate failed-interaction loss, support and recovery cost, future revenue at risk, and contribution margin at risk
- A deterministic synthetic potential-outcomes benchmark used to evaluate estimator recovery without mutating source commerce data
- Self-validating Parquet artifacts bound to the exact source dataset and resolved impact configuration

The standard reference build analyses 3,000 customers, identifies 103 incident-exposed customers, recovers a positive synthetic churn effect with its bootstrap interval covering the known benchmark effect, and produces fully reconciled financial-impact components. These are synthetic evaluation results, not production claims.

### Operational evidence and lineage

- Source-bound service-health, deployment, configuration, feature-flag, runbook, and historical-incident evidence
- Canonical payload hashes, trust levels, supporting/contradictory/contextual roles, and deterministic incident timelines
- A validated lineage DAG with reference, cycle, orphan, and reachability checks
- Optional binding to analytics, detection, and customer-impact artifacts from the same dataset

### Governed Tool Gateway

- Strict read-only tool schemas and deny-by-default role authorization
- AST-validated in-memory DuckDB SQL over explicitly registered artifact tables
- Deterministic evidence, lineage, change, runbook, anomaly, impact, and artifact tools
- Bounded rows, bytes, complexity, memory, and execution time
- Canonical responses and an independently verifiable hash-chained invocation ledger

### Probabilistic agentic investigation

- Provider-neutral OpenAI-compatible routing: Groq GPT-OSS is the tested live adapter, while NVIDIA NIM remains an optional adapter with offline coverage
- A bounded single-agent loop that can call only the Governed Tool Gateway
- Competing hypotheses, explicit falsifiers, contradictory-evidence search, and unsupported-citation rejection
- Deterministic posterior ranking, entropy, confidence, and abstention outside the language model
- Source-bound reports, hash-chained transcripts, validation, replay, and benchmark metrics
- Fully offline scripted-provider CI; live API access is optional and uses an environment-only key

### Governed remediation and human approval

- Strict reversible action proposals bound to a validated, concluded investigation
- Deterministic confidence, evidence, state-precondition, blast-radius, and risk policy
- Immutable source-bound remediation plans and closed-world artifact validation
- Append-only human approval decisions with requester separation, trusted per-identity attestations, and rejection vetoes
- Short-lived HMAC approval tokens bound to the exact approval snapshot and action set
- Atomic simulated deployment rollback, feature-flag, and configuration execution
- One-time token and plan replay protection plus fresh inverse rollback proposals
- No production infrastructure mutation; recovery verification remains a separate capability

### Deterministic recovery verification and reopening

- Source-bound primary and guardrail observations with strict pre/post windows
- Robust baselines, equivalence tests, sustained recovery bands, adverse trends, and severe-breach handling
- Closed-world recovery artifacts with deterministic semantic replay
- Immutable local lifecycle generations and automatic reopening after verified regression
- Recovery authority remains ordinary code; execution success and model prose cannot declare recovery

### Hidden-benchmark evaluation and adversarial testing

- Separate agent-visible cases and evaluator-only answer keys
- Top-k, reciprocal-rank, complete-outcome Brier, clipped log-loss, reliability/ECE, abstention, coverage/risk, evidence, safety, remediation, and recovery metrics
- Source-bound closed-world evaluation and comparison artifacts with semantic replay
- Real input/configuration ablations and deterministic paired bootstrap intervals
- SQL, path, artifact, approval, token, remediation, and recovery adversarial boundaries

### Read-only terminal control room (Phase 11 in progress)

- Plain-language status across data, detection, impact, evidence, investigation, remediation, recovery, and evaluation
- Existing validators and authoritative replay functions remain the only source of truth
- Deterministic JSON snapshots for CI, regression fixtures, and support bundles
- Safe workspace-relative paths, ASCII/no-color modes, EOF and interrupt handling
- No approval, execution, rollback, recovery, shell, cloud, or secret authority

## Quick start

### Requirements

- Python 3.11 or newer
- `make` is optional; every command is also available through Python

### Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Generate a smoke dataset

```bash
paic simulate \
  --config configs/simulation/smoke.yaml \
  --output-dir data/generated/smoke
```

The command creates:

```text
data/generated/smoke/
├── _SUCCESS
├── config.resolved.json
├── manifest.json
└── tables/
    ├── customers.parquet
    ├── checkout_sessions.parquet
    ├── payment_attempts.parquet
    ├── orders.parquet
    └── ... 13 more tables
```

### Validate and inspect the dataset

```bash
paic dataset validate --dataset-dir data/generated/smoke
paic dataset summary --dataset-dir data/generated/smoke
```

### Build and validate the analytical artifact

```bash
paic analytics build \
  --dataset-dir data/generated/smoke \
  --config configs/analytics/smoke.yaml \
  --output-dir data/generated/analytics-smoke

paic analytics validate \
  --analytics-dir data/generated/analytics-smoke \
  --dataset-dir data/generated/smoke

paic analytics summary \
  --analytics-dir data/generated/analytics-smoke
```

The analytical build writes:

```text
data/generated/analytics-smoke/
├── _SUCCESS
├── analytics.config.resolved.json
├── manifest.json
├── metric_catalog.json
└── tables/
    ├── metric_observations.parquet
    ├── funnel_observations.parquet
    ├── contribution_observations.parquet
    └── data_quality_results.parquet
```

### Build and validate the statistical detector

```bash
paic detection build \
  --analytics-dir data/generated/analytics-smoke \
  --config configs/detection/smoke.yaml \
  --output-dir data/generated/detection-smoke

paic detection validate \
  --detection-dir data/generated/detection-smoke \
  --analytics-dir data/generated/analytics-smoke

paic detection summary \
  --detection-dir data/generated/detection-smoke
```

The detection artifact contains scored observations, anomaly events, change-point events, benchmark truth and results, detector quality evidence, the resolved detector configuration, cryptographic hashes, and source-analytics lineage.

### Generate, analyse, and benchmark the larger baseline

```bash
paic simulate \
  --config configs/simulation/standard.yaml \
  --output-dir data/generated/standard

paic analytics build \
  --dataset-dir data/generated/standard \
  --config configs/analytics/standard.yaml \
  --output-dir data/generated/analytics-standard

paic detection build \
  --analytics-dir data/generated/analytics-standard \
  --config configs/detection/standard.yaml \
  --output-dir data/generated/detection-standard

paic detection validate \
  --detection-dir data/generated/detection-standard \
  --analytics-dir data/generated/analytics-standard
```

### Build and validate customer impact

```bash
paic simulate \
  --config configs/simulation/impact-smoke.yaml \
  --output-dir data/generated/impact-source-smoke

paic impact build \
  --dataset-dir data/generated/impact-source-smoke \
  --config configs/impact/smoke.yaml \
  --output-dir data/generated/impact-smoke

paic impact validate \
  --impact-dir data/generated/impact-smoke \
  --dataset-dir data/generated/impact-source-smoke

paic impact summary \
  --impact-dir data/generated/impact-smoke
```

The impact artifact contains customer features, survival curves, Cox coefficients, propensity scores, causal estimates, segment impact, financial impact, model metrics, quality evidence, hashes, and source lineage. See [`docs/CUSTOMER_IMPACT.md`](docs/CUSTOMER_IMPACT.md).

### Validate the project contracts

```bash
paic validate --spec-dir specs
paic summary --spec-dir specs
```

### Run the quality suite

```bash
make check
```

Equivalent commands:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest --cov=paic --cov-report=term-missing
```

## Generated data model

| Area | Tables |
|---|---|
| Customers and catalogue | `customers`, `sellers`, `warehouses`, `products`, `promotions` |
| Commerce funnel | `checkout_sessions`, `payment_attempts`, `orders`, `order_items` |
| Inventory and fulfilment | `inventory_snapshots`, `shipments`, `warehouse_scan_events`, `returns`, `refunds` |
| Operational context | `seller_feed_runs`, `pipeline_runs`, `deployments` |

See [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) for table-level details and [`docs/SYNTHETIC_COMMERCE_ENVIRONMENT.md`](docs/SYNTHETIC_COMMERCE_ENVIRONMENT.md) for modelling assumptions.

## Target architecture

```text
Synthetic commerce environment
          |
          v
Metric and cohort calculations
          |
          v
Deterministic statistical detectors
          |
          v
Structured incident state
          |
          v
Incident Commander agent
          |
          +------ Safe SQL
          +------ Logs and service metrics
          +------ Deployments and configuration history
          +------ Data and service lineage
          +------ Historical incidents and runbooks
          +------ Statistical validation tools
          |
          v
Probabilistic root-cause ranking
          |
          v
Policy and human approval gate
          |
          v
Simulated remediation
          |
          v
Statistical recovery verification
          |
          v
Evidence-backed incident report and evaluator
```

## Example investigation

A checkout conversion metric falls sharply for Android customers in one region shortly after several operational changes.

The implemented system can:

1. detect that the decline is statistically unusual,
2. identify the affected geography, device, and application version,
3. inspect the conversion funnel to locate the failing step,
4. generate multiple falsifiable hypotheses,
5. query approved data through a read-only SQL gateway,
6. inspect deployments, logs, configuration changes, and lineage,
7. search for evidence that contradicts each explanation,
8. rank likely root causes probabilistically,
9. estimate affected customers, lost orders, churn exposure, and revenue risk,
10. request human approval for a reversible remediation,
11. apply the remediation in the simulation,
12. verify sustained recovery across primary and guardrail metrics,
13. generate an evidence-linked incident report.

## Core design principles

### Deterministic detection

Metric calculation, anomaly detection, statistical testing, permissions, approval enforcement, and recovery verification belong in ordinary code. Language models are reserved for planning, hypothesis generation, tool selection, interpretation, and report drafting.

### Evidence before conclusions

Every root-cause hypothesis must define expected observations, planned tests, supporting evidence, contradictory evidence, and a reason to accept, reject, or retain it.

### Explicit uncertainty

Root-cause probabilities are calculated outside the language model and evaluated for calibration against hidden ground truth. A confidence value is useful only when its reliability is measured.

### Bounded autonomy

The agent may investigate autonomously through approved tools. It may recommend sensitive actions, but policy code decides whether an action is automatic, requires exact human approval, or is blocked.

### Measured recovery

Successful execution is not the same as successful remediation. Recovery requires adequate sample size, sustained improvement, return toward an expected statistical range, and healthy guardrail metrics.

Recovery observations are source-authoritative: analytics rows strictly before
the execution timestamp provide baseline evidence, while post-action values are
explicitly evaluator-generated synthetic evidence from a resolved scenario.
Structural artifact validation is distinct from authoritative source replay;
observation validation requires the bound analytics and execution artifacts,
and recovery validation additionally requires the observation artifact. Missing
evidence emits a separate lifecycle gap trigger and does not count as an
observed regression by default.

## Safety model

The project operates entirely on synthetic systems and simulated remediations. Its intended boundaries include:

- no unrestricted credentials for the language model,
- read-only investigative SQL,
- parsed and policy-checked queries,
- approved schemas, row limits, timeouts, and audit records,
- exact human approval for reversible sensitive actions,
- blocked high-risk actions,
- untrusted treatment of logs, runbooks, and retrieved text,
- explicit protection against prompt injection and fabricated evidence.

See [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md).

## Evaluation

Each benchmark incident has hidden ground truth so system behaviour can be measured objectively. The statistical detector already reports scenario recall, observation precision, false-positive rate, point recall, and detection delay. The standard deterministic benchmark currently produces:

| Measure | Result |
|---|---:|
| Injected scenarios | 10 |
| Scenario recall | 100% |
| Observation precision | 81.25% |
| False-positive rate | 0.24% |
| Mean detection delay | 1.2 periods |

The expanded offline evaluator now measures:

- root-cause Top-1 and Top-3 accuracy, reciprocal rank, and hypothesis-set recall,
- complete-outcome Brier score, clipped log loss, reliability bins, and expected calibration error,
- abstention accuracy, coverage, selective accuracy, and selective risk,
- evidence citation validity, required-evidence coverage, and unsupported claims,
- governed tool calls, tool failures, tool-budget failures, and authority violations,
- remediation and recovery correctness,
- paired baseline/ablation deltas with deterministic descriptive bootstrap intervals,
- source-bound semantic replay and adversarial SQL, path, artifact, approval, token, remediation, and recovery boundaries.

Synthetic evaluator scores demonstrate reproducibility and control integrity; they are not production model-quality claims. Token, latency, and cost fields are populated only from real provider metadata.

No README or résumé result should be published until a reproducible benchmark command produces it.

## Repository map

```text
configs/                Reproducible simulation, analytics, detection, and impact configurations
specs/                  Product, evaluation, safety, and incident contracts
src/paic/contracts/     Contract models, loaders, and cross-contract validation
src/paic/simulator/     Synthetic commerce generation, schemas, export, and validation
src/paic/analytics/     Semantic metrics, cohorts, funnels, contributions, and quality checks
src/paic/detection/     Statistical baselines, predictive tests, FDR, change detection, and benchmarks
src/paic/impact/        Customer features, survival, causal estimates, and financial impact
src/paic/evidence/      Operational evidence, lineage, timelines, and source binding
src/paic/tools/         Governed read-only tools, SQL policy, authorization, and audit ledger
src/paic/investigation/ Model routing, tool orchestration, probability, and source-bound reports
src/paic/remediation/   Policy, approvals, tokens, reversible simulated execution, and state lineage
src/paic/recovery/      Statistical recovery, authoritative observations, artifacts, and reopening lifecycle
src/paic/evaluation/    Hidden benchmarks, scoring, ablations, comparisons, replay, and adversarial tests
src/paic/tui/           Read-only terminal workspace inspection and deterministic snapshots
schemas/                Generated JSON Schemas
examples/               Small programmatic usage examples
tests/                  Unit, invariant, CLI, reconciliation, integrity, and adversarial tests
docs/                   Architecture, data, analytics, detection, impact, evidence, agents, security, and decisions
.github/                 Continuous integration and contribution templates
```

## Development roadmap

The remaining delivery sequence is:

1. complete the developer TUI, exhaustive lifecycle testing, endurance testing, and artifact-publication hardening;
2. add Docker and production engineering, including observability, persistent services, identity, secrets, and deployment testing;
3. build the public web product only after the TUI and containerized system meet their reliability gates.

Progress and boundaries are tracked in [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md) and [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md).

## Contributing

Issues and pull requests are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes. New functionality should preserve deterministic tests, explicit contracts, documented assumptions, and measurable acceptance criteria.

## License

This project is available under the [MIT License](LICENSE).

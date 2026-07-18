# Current Status

## Available now

### Executable contracts

- Product, evaluation, safety, and incident YAML specifications
- Five seed incidents covering checkout, payments, inventory, analytics pipelines, and fulfilment
- Hidden ground truth, decoy changes, competing hypotheses, evidence expectations, remediation, and recovery criteria
- Strict Pydantic models, cross-file validation, CLI summaries, and generated JSON Schemas

### Synthetic commerce environment

- Deterministic configuration-driven generation with fixed seeds
- Seventeen relational commerce and operational tables
- Customers, catalogue, promotions, checkout funnels, payments, orders, inventory, fulfilment scans, returns, refunds, seller feeds, pipelines, and deployments
- Time-of-day, weekday, regional, device, channel, customer-type, preference, and product-demand variation
- Canonical schemas, relational constraints, UTC timestamps, and reproducible identifiers
- Parquet export with resolved configuration, runtime metadata, table hashes, and a tamper-evident manifest
- Validation for schemas, keys, temporal order, financial reconciliation, inventory balance, healthy baselines, configuration drift, and file corruption

### Analytics and metric layer

- Forty-three deterministic metrics across nine operational domains
- Hourly and daily observations with explicit numerator, denominator, sample size, and quality status
- Overall, one-dimensional, and two-dimensional cohort analysis
- Five-stage checkout funnel with stepwise conversion and drop-off
- Adjacent-period contribution decomposition separating rate and population-mix effects
- Reconciliation of supported cohort totals to overall observations
- Source, semantic-model, arithmetic, range, funnel, and contribution quality checks
- Self-validating analytical artifacts containing Parquet tables, resolved configuration, metric catalog, manifest, hashes, and runtime metadata
- CLI commands for analytical build, validation, and summary

### Statistical anomaly detection

- Rolling and seasonal no-lookahead baselines with robust median/MAD estimates
- Empirical beta-binomial predictive tests for ratio metrics
- Poisson and negative-binomial predictive tests for counts
- Robust log-Student-t tests for currency and duration metrics
- Benjamini-Hochberg false-discovery control by time grain and period
- Two-sided CUSUM and sequential likelihood evidence
- Metric-specific eligibility, sample-size, effect-size, and detector-support policies
- Anomaly event and change-point event formation
- Ten deterministic evaluator scenarios with hidden ground truth
- Reference standard benchmark: 100% scenario recall, 81.25% precision, 0.24% false-positive rate, and 1.2-period mean delay
- Source-bound, self-validating Parquet artifacts and detector CLI commands

### Customer impact, survival, and causal analysis

- Incident-linked exposure cohorts derived from actual customer interactions
- Configurable churn horizon, pre-incident feature window, and forward value horizon
- Customer-level RFM, tenure, spend, discount, category, checkout, payment, fulfilment, return, and refund features
- Kaplan–Meier curves with Greenwood intervals and regularized Cox proportional-hazards estimates
- Propensity-score matching, stabilized inverse-probability weighting, difference-in-differences, and shifted-window placebo analysis
- Bootstrap confidence intervals, covariate-balance diagnostics, concordance, and Brier scoring
- Immediate revenue loss, support and recovery cost, incremental churn, future revenue, contribution margin, and total financial impact
- Evaluator-only potential-outcomes benchmark with exact synthetic ATT
- Source-bound, self-validating Parquet artifacts and impact CLI commands
- Reference impact benchmark: 3,000 customers, 103 exposed, synthetic ATT 0.2427, stabilized-IPW estimate 0.1829 with a 95% bootstrap interval of 0.0696 to 0.2945, Cox concordance 0.6362, Brier score 0.1152, weighted maximum absolute SMD 0.2478, placebo magnitude 0.0466, and zero quality failures

## Explicit limitations

- The simulator produces healthy baseline data only; raw-event incident injection is not yet implemented. Detector benchmarks currently use evaluator-only metric perturbations.
- No language model, agent framework, database service, frontend, Docker service, or cloud resource is required yet.
- Generated data and analytical outputs are development benchmarks, not production forecasts.

## Next capability

The next implementation adds operational evidence, lineage, historical incident retrieval, and safe read-only tool access.

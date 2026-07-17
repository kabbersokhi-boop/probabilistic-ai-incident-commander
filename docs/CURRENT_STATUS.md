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

## Explicit limitations

- The simulator produces healthy baseline data only; incident injection is not yet implemented.
- Statistical anomaly detection is not yet implemented.
- Churn, survival, causal impact, and revenue-at-risk models are not yet implemented.
- No language model, agent framework, database service, frontend, Docker service, or cloud resource is required yet.
- Generated data and analytical outputs are development benchmarks, not production forecasts.

## Next capability

The next implementation adds robust seasonal and cohort-aware anomaly detection, change-point estimation, sequential monitoring, and false-discovery control over the deterministic metric observations.

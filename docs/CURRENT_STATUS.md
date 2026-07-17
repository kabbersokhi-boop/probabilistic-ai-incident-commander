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
- Customers, catalogue, promotions, campaign attribution, checkout funnels, payments, orders, inventory, fulfilment scans, returns, refunds, seller feeds, pipelines, and deployments
- Time-of-day, day-of-week, regional, device, channel, customer-type, customer-preference, and product-demand variation
- Canonical schemas, primary and foreign keys, UTC timestamps, and reproducible identifiers
- Parquet export with resolved configuration, manifest, row counts, schemas, relationships, timestamps, and SHA-256 hashes
- Validation for schema integrity, keys, temporal order, financial reconciliation, inventory balance, healthy baseline metrics, configuration drift, and file corruption
- CLI generation, validation, and dataset summaries

## Explicit limitations

- The simulator produces healthy baseline data only; incident injection is not yet implemented.
- The analytical metric layer and statistical anomaly detectors are not yet implemented.
- No language model, agent framework, database service, frontend, Docker service, or cloud resource is required yet.
- Generated datasets are intended for development and benchmarking, not production forecasting.

## Next capability

The next implementation adds a semantic analytical layer for business metrics, checkout funnels, cohorts, contribution analysis, and data-quality status on top of the generated Parquet datasets.

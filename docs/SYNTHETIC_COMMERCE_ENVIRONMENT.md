# Synthetic Commerce Environment

## Purpose

The simulator creates a realistic but fully artificial commerce environment for statistical development, agent evaluation, security testing, and demonstrations. It contains no customer data and requires no external service.

Its first responsibility is to produce a healthy baseline. Failure injection is intentionally separate so normal variation, injected anomalies, and evaluator-only ground truth remain distinguishable.

## Reproducibility

A simulation is determined by:

- the validated YAML configuration,
- a top-level integer seed,
- the package version.

Randomness is namespaced by domain. Customer generation, product generation, checkout behaviour, payments, inventory, and operations receive stable independent random streams. Adding a random draw in one domain therefore does not silently reorder every other domain.

The same supported code version, dependency set, configuration, and seed must produce identical logical tables. Exported Parquet bytes are additionally hashed in the manifest.

## Normal behaviour modelled

### Demand

- Hour-of-day traffic profile
- Weekday and weekend differences
- Regional traffic weights and conversion multipliers
- Product demand weights
- Promotion exposure by channel, region, and category

### Customers

- Home region
- Preferred device and payment method
- Loyalty tier and acquisition channel
- Activity, price-sensitivity, and return-propensity scores
- New-versus-returning session behaviour and preference adherence

### Checkout and payments

- Address, inventory, payment, and order-completion stages
- Stage-specific abandonment reasons
- Device, channel, campaign attribution, customer type, application version, issuer, processor, latency, fraud score, and rule version
- Region-sensitive payment approval probabilities

### Commerce and fulfilment

- Multi-item orders with discounts, tax, shipping, and reconciled totals
- Daily inventory snapshots and seller-reported availability
- Regional warehouse assignment, delivery service, carriers, promises, delivery timing, and scan counts
- Ordered warehouse scan events with source timestamps, platform receipt timestamps, connector batches, and ingestion lag
- Product- and customer-sensitive returns and corresponding refunds

### Operational context

- Seller feed runs with schema versions, accepted and rejected records, latency, and status
- Analytical pipeline runs with input/output counts, quality result, and code version
- Routine service deployments with regional scope and rollback availability

## Configuration profiles

- `configs/simulation/smoke.yaml`: small deterministic dataset for tests and local verification
- `configs/simulation/standard.yaml`: 14-day baseline that produces roughly 100,000 linked rows for analytics development and demonstrations

Every configuration is strict: unknown fields are rejected, UTC boundaries are required, catalogues must contain unique values, and entity counts must form a coherent environment.

## Output layout

```text
<dataset>/
├── _SUCCESS
├── config.resolved.json
├── manifest.json
└── tables/
    └── <table>.parquet
```

The manifest records:

- simulation, generator, Python, and dependency versions,
- logical start and end timestamps,
- seed and configuration hash,
- incident injection count,
- table paths, row counts, byte sizes, and file hashes,
- primary keys, foreign keys, columns, data types, and timestamp ranges.

## Validation

The validator checks:

- required table presence and exact canonical schemas,
- primary-key uniqueness and foreign-key integrity,
- temporal ordering across funnels and operational runs,
- payment, order, item, tax, shipping, and total reconciliation,
- inventory equations and seller-feed consistency,
- healthy conversion, approval, and delivery rates,
- simulation-window boundaries,
- manifest row counts, configuration hash, and Parquet file hashes,
- absence of incident injections in baseline datasets.

## Deliberate limitations

The current generator is designed for reproducible analytical experiments, not exact reproduction of any real marketplace. It does not yet model incident injections, service logs, distributed traces, streaming infrastructure, or long-term customer churn outcomes. Those are introduced as separately evaluated capabilities.

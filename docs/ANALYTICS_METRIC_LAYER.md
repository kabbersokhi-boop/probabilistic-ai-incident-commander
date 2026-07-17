# Analytics and Metric Layer

The analytical layer turns the synthetic commerce tables into stable, auditable business observations. It is deliberately deterministic: no language model participates in metric calculation, cohort construction, funnel arithmetic, contribution analysis, or quality decisions.

## Outputs

Each build writes four canonical Parquet tables:

| Table | Purpose |
|---|---|
| `metric_observations` | Long-form metric values by time grain and supported cohort |
| `funnel_observations` | Checkout-stage counts, conversion, and drop-off |
| `contribution_observations` | Adjacent-period rate and population-mix effects |
| `data_quality_results` | Source, model, arithmetic, reconciliation, and integrity checks |

Every metric observation contains:

- metric identity and business domain,
- hourly or daily period bounds,
- cohort identity and populated dimensions,
- value,
- numerator and denominator where mathematically meaningful,
- sample size,
- explicit quality status.

This makes downstream statistical detection independent of hidden SQL or narrative interpretation.

## Metric domains

The catalog contains 43 metrics covering:

- checkout progression and conversion,
- payment approval, declines, fraud rejection, and latency,
- order volume, value, items, discounts, and returns,
- inventory accuracy, availability, and feed gaps,
- shipment timeliness, delivery duration, and scan ingestion delay,
- returns and refund completion,
- seller-feed health,
- analytical pipeline health,
- deployment activity and success.

The machine-readable catalog is exported with every artifact and documented in [`METRIC_CATALOG.md`](METRIC_CATALOG.md).

## Cohorts

The standard configuration supports overall observations plus cohorts such as region, device, channel, customer type, app version, issuer, payment method, processor, customer segment, loyalty tier, product category, seller, warehouse, pipeline, service, deployment scope, and region-by-device.

A metric is only calculated for cohorts supported by its source fact. Unsupported joins are never fabricated.

## Funnel

The checkout funnel is:

```text
checkout_started
    -> address_submitted
    -> inventory_checked
    -> payment_started
    -> order_completed
```

For every configured cohort and period, the system records:

- stage count,
- previous-stage count,
- conversion from the previous stage,
- conversion from the start,
- drop-off count and rate,
- quality status.

Monotonicity and arithmetic are validated independently.

## Contribution decomposition

For selected ratio metrics, adjacent periods are decomposed into:

- **rate effect:** change within a cohort,
- **mix effect:** change caused by cohort population share,
- **total contribution:** the sum of both effects.

The implementation uses a symmetric Kitagawa-style decomposition. Contributions must reconstruct the overall metric change within numerical tolerance or the build fails.

## Quality gates

The build verifies:

- source dataset validity,
- analytical fact cardinality and key uniqueness,
- required metric columns and joined-dimension coverage,
- metric catalog coverage,
- finite values and documented ranges,
- stored numerator/denominator arithmetic,
- cohort-to-overall reconciliation for additive statistics,
- funnel monotonicity and formulas,
- contribution reconstruction.

Hard failures prevent artifact publication. Empty source domains, such as a one-day smoke dataset with no returns, are reported as warnings rather than invented observations.

## Commands

```bash
paic analytics build \
  --dataset-dir data/generated/standard \
  --config configs/analytics/standard.yaml \
  --output-dir data/generated/analytics-standard

paic analytics validate \
  --analytics-dir data/generated/analytics-standard \
  --dataset-dir data/generated/standard

paic analytics summary \
  --analytics-dir data/generated/analytics-standard
```

## Reproducibility

The artifact includes the exact resolved configuration, metric catalog, source manifest identity, runtime dependency versions, table schemas, row counts, timestamp bounds, byte sizes, SHA-256 hashes, and a success marker tied to the manifest. Repeated builds from the same inputs are tested for identical logical and file-level outputs within the same runtime.

# Metric Catalog

This catalog documents the deterministic business metrics emitted by the analytical layer. The machine-readable source of truth is `src/paic/analytics/registry.py`, and every exported analytical artifact includes the same catalog as JSON.

Each ratio or mean preserves its numerator and denominator. Quantile metrics preserve their sample size. All metrics are calculated from canonical analytical facts without language-model involvement.

## Checkout

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `checkout_sessions` | `count` | `sessions` | Checkout sessions started in the period. |
| `unique_checkout_customers` | `distinct_count` | `customers` | Distinct customers who started checkout. |
| `address_submission_rate` | `ratio` | `proportion` | Share of checkout sessions that submitted an address. |
| `inventory_check_rate` | `ratio` | `proportion` | Share of checkout sessions that reached inventory validation. |
| `payment_reach_rate` | `ratio` | `proportion` | Share of checkout sessions that reached payment. |
| `checkout_conversion_rate` | `ratio` | `proportion` | Share of checkout sessions that produced at least one order. |

## Data Pipelines

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `pipeline_success_rate` | `ratio` | `proportion` | Share of analytical pipeline runs completed successfully. |
| `pipeline_quality_pass_rate` | `ratio` | `proportion` | Share of pipeline runs whose data-quality checks passed. |
| `pipeline_row_retention_rate` | `ratio_of_sums` | `proportion` | Output rows divided by input rows. |
| `pipeline_duration_p95_seconds` | `quantile` | `seconds` | 95th percentile pipeline run duration. |

## Deployments

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `deployment_count` | `count` | `deployments` | Service deployments recorded in the period. |
| `deployment_success_rate` | `ratio` | `proportion` | Share of deployments with successful status. |

## Fulfilment

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `shipments` | `count` | `shipments` | Shipments created in the period. |
| `on_time_delivery_rate` | `ratio` | `proportion` | Share of delivered shipments that arrived on or before the promise. |
| `late_delivery_rate` | `ratio` | `proportion` | Share of delivered shipments that arrived after the promise. |
| `delivery_duration_p50_hours` | `quantile` | `hours` | Median elapsed time from shipment to delivery. |
| `delivery_duration_p95_hours` | `quantile` | `hours` | 95th percentile elapsed time from shipment to delivery. |
| `scan_ingestion_lag_p95_seconds` | `quantile` | `seconds` | 95th percentile delay between source and platform receipt for warehouse scans. |

## Inventory

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `inventory_exact_match_rate` | `ratio` | `proportion` | Share of snapshots where feed-reported and available quantities match. |
| `out_of_stock_rate` | `ratio` | `proportion` | Share of inventory snapshots with no available units. |
| `low_stock_rate` | `ratio` | `proportion` | Share of snapshots at or below the product reorder point. |
| `mean_inventory_gap_units` | `mean` | `units` | Mean absolute difference between feed-reported and available inventory. |

## Orders

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `completed_orders` | `count` | `orders` | Orders created in the period. |
| `gross_order_value` | `sum` | `currency_units` | Sum of completed order totals. |
| `average_order_value` | `mean` | `currency_units` | Mean completed order total. |
| `average_items_per_order` | `mean` | `items_per_order` | Mean item count per order. |
| `discount_share` | `ratio_of_sums` | `proportion` | Discount amount divided by pre-discount subtotal. |
| `units_sold` | `sum` | `units` | Units represented by order items. |
| `item_revenue` | `sum` | `currency_units` | Revenue represented by order-item line totals. |

## Payments

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `payment_attempts` | `count` | `attempts` | Payment authorization attempts in the period. |
| `payment_approval_rate` | `ratio` | `proportion` | Share of payment attempts approved. |
| `payment_decline_rate` | `ratio` | `proportion` | Share of payment attempts declined. |
| `risk_rejection_rate` | `ratio` | `proportion` | Share of payment attempts rejected by fraud controls. |
| `payment_latency_p50_ms` | `quantile` | `milliseconds` | Median payment-processor latency. |
| `payment_latency_p95_ms` | `quantile` | `milliseconds` | 95th percentile payment-processor latency. |

## Returns

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `returned_order_rate` | `ratio` | `proportion` | Share of orders with at least one return request. |
| `return_requests` | `count` | `returns` | Return requests created in the period. |
| `return_completion_rate` | `ratio` | `proportion` | Share of returns physically received. |
| `refund_completion_rate` | `ratio` | `proportion` | Share of returns with a completed refund. |
| `refund_processing_p50_hours` | `quantile` | `hours` | Median elapsed time from refund initiation to completion. |

## Seller Feeds

| Metric | Calculation | Unit | Description |
|---|---|---|---|
| `feed_success_rate` | `ratio` | `proportion` | Share of seller-feed runs completed successfully. |
| `feed_rejection_rate` | `ratio_of_sums` | `proportion` | Rejected records divided by received records. |
| `feed_latency_p95_seconds` | `quantile` | `seconds` | 95th percentile seller-feed run latency. |

## Cohort support

Metrics declare their permitted cohort dimensions. The engine calculates only intersections between configured cohorts and supported metric dimensions. This prevents accidental many-to-many joins or misleading breakdowns.

## Quality semantics

- `ok`: sufficient observations and valid arithmetic.
- `insufficient_data`: the metric is valid but below the configured minimum sample size.
- `undefined`: the denominator or usable sample is zero.
- `invalid`: a hard arithmetic or structural invariant failed; valid builds contain no such rows.

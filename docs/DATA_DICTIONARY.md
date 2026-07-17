# Data Dictionary

All timestamps use UTC. Canonical columns and Polars data types are defined in `src/paic/simulator/schema.py`; exported manifests repeat the schema and relationship metadata for each dataset.

| Table | Primary key | Purpose and important relationships |
|---|---|---|
| `customers` | `customer_id` | Customer region, preferences, loyalty, acquisition, segment, activity, price sensitivity, and return propensity |
| `sellers` | `seller_id` | Seller identity, category focus, tier, rating, feed format, schema version, and reliability |
| `warehouses` | `warehouse_id` | Regional capacity, service level, scan reliability, and activation date |
| `products` | `product_id` | Catalogue price, cost, demand, return propensity, reorder point; references seller and warehouse |
| `promotions` | `promotion_id` | Channel-, region-, and category-scoped campaigns with active window, discount, and budget |
| `checkout_sessions` | `session_id` | Funnel timestamps, stage reached, abandonment reason, device, channel, campaign, customer type, and application version; references customer, product, and optional promotion |
| `payment_attempts` | `payment_attempt_id` | Method, issuer, processor, amount, approval, decline reason, latency, fraud score, and rule version; references session and customer |
| `orders` | `order_id` | Completed transaction with propagated channel and campaign attribution plus reconciled subtotal, discount, tax, shipping, total, currency, and item count; references session, customer, and payment |
| `order_items` | `order_item_id` | Product-level quantity, price, discount, and line total; references order, product, and seller |
| `inventory_snapshots` | `snapshot_id` | Daily on-hand, reserved, available, reorder, and feed-reported quantity; references product and warehouse |
| `shipments` | `shipment_id` | Carrier, delivery service, ship time, promise, delivery, status, scan count, and lateness; references order and warehouse |
| `warehouse_scan_events` | `scan_event_id` | Ordered warehouse and carrier handoff events with source time, platform receipt time, connector batch, and ingestion lag; references shipment, order, and warehouse |
| `returns` | `return_id` | Return request, receipt, reason, status, and expected refund; references order, order item, and customer |
| `refunds` | `refund_id` | Refund initiation, completion, status, amount, and processor; references return and order |
| `seller_feed_runs` | `feed_run_id` | Seller catalogue feed timing, schema, received/rejected records, status, and latency; references seller |
| `pipeline_runs` | `pipeline_run_id` | Analytical pipeline timing, row flow, status, quality result, and code version |
| `deployments` | `deployment_id` | Routine service deployment version, scope, time, status, change type, and rollback availability |

## Relationship sketch

```text
customers -> checkout_sessions -> payment_attempts -> orders -> order_items
                    |                                  |          |
products -----------+                                  |          +-> products -> sellers
promotions ----------                                  +-> shipments -> warehouses
orders -> shipments -> warehouse_scan_events
orders -> returns -> refunds
products -> inventory_snapshots -> warehouses
sellers -> seller_feed_runs
```

## Hidden truth rule

Baseline datasets do not contain incident IDs, root causes, injection flags, or evaluator-only labels. Future incident ground truth will be stored outside agent-accessible tables.

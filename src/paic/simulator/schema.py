"""Canonical table contracts for generated commerce data."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class ForeignKey:
    column: str
    target_table: str
    target_column: str
    nullable: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[tuple[str, pl.DataType], ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...] = ()
    timestamp_columns: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return dict(self.columns)


UTC_DATETIME = pl.Datetime(time_unit="us", time_zone="UTC")


TABLE_SPECS: dict[str, TableSpec] = {
    "customers": TableSpec(
        name="customers",
        columns=(
            ("customer_id", pl.String),
            ("created_at", UTC_DATETIME),
            ("home_region", pl.String),
            ("preferred_device", pl.String),
            ("preferred_payment_method", pl.String),
            ("issuer", pl.String),
            ("loyalty_tier", pl.String),
            ("acquisition_channel", pl.String),
            ("customer_segment", pl.String),
            ("baseline_activity_score", pl.Float64),
            ("baseline_price_sensitivity", pl.Float64),
            ("baseline_return_propensity", pl.Float64),
        ),
        primary_key=("customer_id",),
        timestamp_columns=("created_at",),
    ),
    "sellers": TableSpec(
        name="sellers",
        columns=(
            ("seller_id", pl.String),
            ("seller_name", pl.String),
            ("home_region", pl.String),
            ("category_focus", pl.String),
            ("seller_tier", pl.String),
            ("rating", pl.Float64),
            ("feed_format", pl.String),
            ("feed_schema_version", pl.String),
            ("feed_reliability", pl.Float64),
            ("joined_at", UTC_DATETIME),
        ),
        primary_key=("seller_id",),
        timestamp_columns=("joined_at",),
    ),
    "warehouses": TableSpec(
        name="warehouses",
        columns=(
            ("warehouse_id", pl.String),
            ("region", pl.String),
            ("capacity_units", pl.Int64),
            ("service_level_days", pl.Int64),
            ("scan_reliability", pl.Float64),
            ("active_from", UTC_DATETIME),
        ),
        primary_key=("warehouse_id",),
        timestamp_columns=("active_from",),
    ),
    "products": TableSpec(
        name="products",
        columns=(
            ("product_id", pl.String),
            ("seller_id", pl.String),
            ("warehouse_id", pl.String),
            ("category", pl.String),
            ("base_price", pl.Float64),
            ("unit_cost", pl.Float64),
            ("demand_weight", pl.Float64),
            ("return_propensity", pl.Float64),
            ("reorder_point", pl.Int64),
            ("active_from", UTC_DATETIME),
        ),
        primary_key=("product_id",),
        foreign_keys=(
            ForeignKey("seller_id", "sellers", "seller_id"),
            ForeignKey("warehouse_id", "warehouses", "warehouse_id"),
        ),
        timestamp_columns=("active_from",),
    ),
    "promotions": TableSpec(
        name="promotions",
        columns=(
            ("promotion_id", pl.String),
            ("promotion_name", pl.String),
            ("channel", pl.String),
            ("region", pl.String),
            ("category", pl.String),
            ("start_at", UTC_DATETIME),
            ("end_at", UTC_DATETIME),
            ("discount_pct", pl.Float64),
            ("budget", pl.Float64),
        ),
        primary_key=("promotion_id",),
        timestamp_columns=("start_at", "end_at"),
    ),
    "checkout_sessions": TableSpec(
        name="checkout_sessions",
        columns=(
            ("session_id", pl.String),
            ("customer_id", pl.String),
            ("product_id", pl.String),
            ("promotion_id", pl.String),
            ("campaign_id", pl.String),
            ("region", pl.String),
            ("device", pl.String),
            ("channel", pl.String),
            ("customer_type", pl.String),
            ("app_version", pl.String),
            ("started_at", UTC_DATETIME),
            ("address_submitted_at", UTC_DATETIME),
            ("inventory_checked_at", UTC_DATETIME),
            ("payment_started_at", UTC_DATETIME),
            ("completed_at", UTC_DATETIME),
            ("stage_reached", pl.String),
            ("abandonment_reason", pl.String),
            ("expected_amount", pl.Float64),
        ),
        primary_key=("session_id",),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("product_id", "products", "product_id"),
            ForeignKey("promotion_id", "promotions", "promotion_id", nullable=True),
        ),
        timestamp_columns=(
            "started_at",
            "address_submitted_at",
            "inventory_checked_at",
            "payment_started_at",
            "completed_at",
        ),
    ),
    "payment_attempts": TableSpec(
        name="payment_attempts",
        columns=(
            ("payment_attempt_id", pl.String),
            ("session_id", pl.String),
            ("customer_id", pl.String),
            ("payment_method", pl.String),
            ("issuer", pl.String),
            ("processor", pl.String),
            ("attempted_at", UTC_DATETIME),
            ("amount", pl.Float64),
            ("currency", pl.String),
            ("approved", pl.Boolean),
            ("decline_reason", pl.String),
            ("latency_ms", pl.Int64),
            ("fraud_score", pl.Float64),
            ("fraud_rule_version", pl.String),
        ),
        primary_key=("payment_attempt_id",),
        foreign_keys=(
            ForeignKey("session_id", "checkout_sessions", "session_id"),
            ForeignKey("customer_id", "customers", "customer_id"),
        ),
        timestamp_columns=("attempted_at",),
    ),
    "orders": TableSpec(
        name="orders",
        columns=(
            ("order_id", pl.String),
            ("session_id", pl.String),
            ("customer_id", pl.String),
            ("payment_attempt_id", pl.String),
            ("ordered_at", UTC_DATETIME),
            ("region", pl.String),
            ("channel", pl.String),
            ("campaign_id", pl.String),
            ("status", pl.String),
            ("subtotal", pl.Float64),
            ("discount_amount", pl.Float64),
            ("tax_amount", pl.Float64),
            ("shipping_amount", pl.Float64),
            ("total_amount", pl.Float64),
            ("currency", pl.String),
            ("item_count", pl.Int64),
        ),
        primary_key=("order_id",),
        foreign_keys=(
            ForeignKey("session_id", "checkout_sessions", "session_id"),
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("payment_attempt_id", "payment_attempts", "payment_attempt_id"),
        ),
        timestamp_columns=("ordered_at",),
    ),
    "order_items": TableSpec(
        name="order_items",
        columns=(
            ("order_item_id", pl.String),
            ("order_id", pl.String),
            ("product_id", pl.String),
            ("seller_id", pl.String),
            ("quantity", pl.Int64),
            ("unit_price", pl.Float64),
            ("discount_amount", pl.Float64),
            ("line_total", pl.Float64),
        ),
        primary_key=("order_item_id",),
        foreign_keys=(
            ForeignKey("order_id", "orders", "order_id"),
            ForeignKey("product_id", "products", "product_id"),
            ForeignKey("seller_id", "sellers", "seller_id"),
        ),
    ),
    "inventory_snapshots": TableSpec(
        name="inventory_snapshots",
        columns=(
            ("snapshot_id", pl.String),
            ("product_id", pl.String),
            ("warehouse_id", pl.String),
            ("snapshot_at", UTC_DATETIME),
            ("on_hand_quantity", pl.Int64),
            ("reserved_quantity", pl.Int64),
            ("available_quantity", pl.Int64),
            ("reorder_point", pl.Int64),
            ("feed_reported_quantity", pl.Int64),
        ),
        primary_key=("snapshot_id",),
        foreign_keys=(
            ForeignKey("product_id", "products", "product_id"),
            ForeignKey("warehouse_id", "warehouses", "warehouse_id"),
        ),
        timestamp_columns=("snapshot_at",),
    ),
    "shipments": TableSpec(
        name="shipments",
        columns=(
            ("shipment_id", pl.String),
            ("order_id", pl.String),
            ("warehouse_id", pl.String),
            ("carrier", pl.String),
            ("delivery_service", pl.String),
            ("shipped_at", UTC_DATETIME),
            ("promised_delivery_at", UTC_DATETIME),
            ("delivered_at", UTC_DATETIME),
            ("status", pl.String),
            ("scan_count", pl.Int64),
            ("late", pl.Boolean),
        ),
        primary_key=("shipment_id",),
        foreign_keys=(
            ForeignKey("order_id", "orders", "order_id"),
            ForeignKey("warehouse_id", "warehouses", "warehouse_id"),
        ),
        timestamp_columns=("shipped_at", "promised_delivery_at", "delivered_at"),
    ),
    "warehouse_scan_events": TableSpec(
        name="warehouse_scan_events",
        columns=(
            ("scan_event_id", pl.String),
            ("shipment_id", pl.String),
            ("order_id", pl.String),
            ("warehouse_id", pl.String),
            ("event_type", pl.String),
            ("sequence_number", pl.Int64),
            ("source_event_at", UTC_DATETIME),
            ("platform_received_at", UTC_DATETIME),
            ("ingestion_lag_seconds", pl.Int64),
            ("connector_batch_seconds", pl.Int64),
        ),
        primary_key=("scan_event_id",),
        foreign_keys=(
            ForeignKey("shipment_id", "shipments", "shipment_id"),
            ForeignKey("order_id", "orders", "order_id"),
            ForeignKey("warehouse_id", "warehouses", "warehouse_id"),
        ),
        timestamp_columns=("source_event_at", "platform_received_at"),
    ),
    "returns": TableSpec(
        name="returns",
        columns=(
            ("return_id", pl.String),
            ("order_id", pl.String),
            ("order_item_id", pl.String),
            ("customer_id", pl.String),
            ("requested_at", UTC_DATETIME),
            ("received_at", UTC_DATETIME),
            ("reason", pl.String),
            ("status", pl.String),
            ("refund_amount", pl.Float64),
        ),
        primary_key=("return_id",),
        foreign_keys=(
            ForeignKey("order_id", "orders", "order_id"),
            ForeignKey("order_item_id", "order_items", "order_item_id"),
            ForeignKey("customer_id", "customers", "customer_id"),
        ),
        timestamp_columns=("requested_at", "received_at"),
    ),
    "refunds": TableSpec(
        name="refunds",
        columns=(
            ("refund_id", pl.String),
            ("return_id", pl.String),
            ("order_id", pl.String),
            ("initiated_at", UTC_DATETIME),
            ("completed_at", UTC_DATETIME),
            ("status", pl.String),
            ("amount", pl.Float64),
            ("processor", pl.String),
        ),
        primary_key=("refund_id",),
        foreign_keys=(
            ForeignKey("return_id", "returns", "return_id"),
            ForeignKey("order_id", "orders", "order_id"),
        ),
        timestamp_columns=("initiated_at", "completed_at"),
    ),
    "seller_feed_runs": TableSpec(
        name="seller_feed_runs",
        columns=(
            ("feed_run_id", pl.String),
            ("seller_id", pl.String),
            ("started_at", UTC_DATETIME),
            ("completed_at", UTC_DATETIME),
            ("schema_version", pl.String),
            ("records_received", pl.Int64),
            ("records_rejected", pl.Int64),
            ("status", pl.String),
            ("latency_seconds", pl.Int64),
        ),
        primary_key=("feed_run_id",),
        foreign_keys=(ForeignKey("seller_id", "sellers", "seller_id"),),
        timestamp_columns=("started_at", "completed_at"),
    ),
    "pipeline_runs": TableSpec(
        name="pipeline_runs",
        columns=(
            ("pipeline_run_id", pl.String),
            ("pipeline_name", pl.String),
            ("started_at", UTC_DATETIME),
            ("completed_at", UTC_DATETIME),
            ("input_rows", pl.Int64),
            ("output_rows", pl.Int64),
            ("status", pl.String),
            ("data_quality_passed", pl.Boolean),
            ("code_version", pl.String),
        ),
        primary_key=("pipeline_run_id",),
        timestamp_columns=("started_at", "completed_at"),
    ),
    "deployments": TableSpec(
        name="deployments",
        columns=(
            ("deployment_id", pl.String),
            ("service", pl.String),
            ("version", pl.String),
            ("region_scope", pl.String),
            ("deployed_at", UTC_DATETIME),
            ("status", pl.String),
            ("change_type", pl.String),
            ("rollback_available", pl.Boolean),
        ),
        primary_key=("deployment_id",),
        timestamp_columns=("deployed_at",),
    ),
}

TABLE_ORDER: tuple[str, ...] = tuple(TABLE_SPECS)


def empty_frame(table_name: str) -> pl.DataFrame:
    """Create an empty frame with the canonical schema for a table."""

    spec = TABLE_SPECS[table_name]
    return pl.DataFrame(schema=spec.schema)


def conform_frame(table_name: str, frame: pl.DataFrame) -> pl.DataFrame:
    """Select canonical columns and cast them to the documented data types."""

    spec = TABLE_SPECS[table_name]
    missing = [name for name, _ in spec.columns if name not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} is missing columns: {', '.join(missing)}")
    expressions = [
        pl.col(name).cast(dtype, strict=True).alias(name) for name, dtype in spec.columns
    ]
    return frame.select(expressions)

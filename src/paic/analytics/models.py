"""Build stable analytical facts from canonical simulator tables."""

from __future__ import annotations

import polars as pl

from paic.analytics.types import FactMap
from paic.simulator.types import FrameMap

_SESSION_DIMENSIONS = (
    "region",
    "device",
    "channel",
    "customer_type",
    "app_version",
    "customer_segment",
    "loyalty_tier",
    "product_category",
    "seller_id",
    "warehouse_id",
)


def _session_context(tables: FrameMap) -> pl.DataFrame:
    customers = tables["customers"].select("customer_id", "customer_segment", "loyalty_tier")
    products = tables["products"].select(
        "product_id",
        "seller_id",
        "warehouse_id",
        pl.col("category").alias("product_category"),
    )
    return (
        tables["checkout_sessions"]
        .join(customers, on="customer_id", how="left", validate="m:1")
        .join(products, on="product_id", how="left", validate="m:1")
    )


def _order_context(tables: FrameMap, sessions: pl.DataFrame) -> pl.DataFrame:
    context_columns = [
        "session_id",
        "device",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
        "product_category",
        "seller_id",
        "warehouse_id",
    ]
    returns = (
        tables["returns"]
        .group_by("order_id")
        .agg(pl.len().cast(pl.Int64).alias("return_request_count"))
    )
    return (
        tables["orders"]
        .join(sessions.select(context_columns), on="session_id", how="left", validate="m:1")
        .join(returns, on="order_id", how="left", validate="1:1")
        .with_columns(
            pl.col("return_request_count").fill_null(0),
            (pl.col("return_request_count").fill_null(0) > 0).alias("has_return"),
        )
    )


def build_facts(tables: FrameMap) -> FactMap:
    """Create one-purpose analytical facts without changing source tables."""

    sessions = _session_context(tables)
    order_session = (
        tables["orders"]
        .group_by("session_id")
        .agg(
            pl.len().cast(pl.Int64).alias("order_count"),
            pl.col("total_amount").sum().alias("session_order_value"),
        )
    )
    checkout = sessions.join(
        order_session, on="session_id", how="left", validate="1:1"
    ).with_columns(
        pl.col("order_count").fill_null(0),
        pl.col("session_order_value").fill_null(0.0),
        pl.col("address_submitted_at").is_not_null().alias("address_submitted"),
        pl.col("inventory_checked_at").is_not_null().alias("inventory_checked"),
        pl.col("payment_started_at").is_not_null().alias("payment_started"),
        (pl.col("order_count").fill_null(0) > 0).alias("has_order"),
    )

    payment_context = sessions.select(
        "session_id",
        "region",
        "device",
        "channel",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
        "product_category",
        "seller_id",
        "warehouse_id",
    )
    payments = (
        tables["payment_attempts"]
        .join(payment_context, on="session_id", how="left", validate="m:1")
        .with_columns(
            (~pl.col("approved")).alias("declined"),
            (pl.col("decline_reason") == "risk_rejected").fill_null(False).alias("risk_rejected"),
        )
    )

    orders = _order_context(tables, sessions)

    order_dimensions = orders.select(
        "order_id",
        "session_id",
        "customer_id",
        "ordered_at",
        "region",
        "channel",
        "device",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
    )
    item_products = tables["products"].select(
        "product_id",
        "warehouse_id",
        pl.col("category").alias("product_category"),
    )
    order_items = (
        tables["order_items"]
        .join(order_dimensions, on="order_id", how="left", validate="m:1")
        .join(item_products, on="product_id", how="left", validate="m:1")
    )

    warehouses = tables["warehouses"].select("warehouse_id", "region")
    products = tables["products"].select(
        "product_id",
        "seller_id",
        "warehouse_id",
        pl.col("category").alias("product_category"),
    )
    inventory = (
        tables["inventory_snapshots"]
        .join(products, on=["product_id", "warehouse_id"], how="left", validate="m:1")
        .join(warehouses, on="warehouse_id", how="left", validate="m:1")
        .with_columns(
            (pl.col("feed_reported_quantity") == pl.col("available_quantity")).alias(
                "inventory_exact_match"
            ),
            (pl.col("available_quantity") <= 0).alias("out_of_stock"),
            (pl.col("available_quantity") <= pl.col("reorder_point")).alias("low_stock"),
            (pl.col("feed_reported_quantity") - pl.col("available_quantity"))
            .abs()
            .cast(pl.Float64)
            .alias("inventory_gap_units"),
        )
    )

    fulfilment_context = orders.select(
        "order_id",
        "session_id",
        "customer_id",
        "region",
        "channel",
        "device",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
    )
    shipments = (
        tables["shipments"]
        .join(fulfilment_context, on="order_id", how="left", validate="1:1")
        .with_columns(
            pl.col("delivered_at").is_not_null().alias("delivered"),
            (pl.col("delivered_at").is_not_null() & ~pl.col("late")).alias("on_time"),
            (pl.col("delivered_at").is_not_null() & pl.col("late")).alias("late_delivered"),
            ((pl.col("delivered_at") - pl.col("shipped_at")).dt.total_seconds() / 3600.0).alias(
                "delivery_duration_hours"
            ),
        )
    )
    scan_context = shipments.select(
        "shipment_id",
        "region",
        "device",
        "channel",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
    )
    scans = tables["warehouse_scan_events"].join(
        scan_context, on="shipment_id", how="left", validate="m:1"
    )

    return_order_context = orders.select(
        "order_id",
        "region",
        "channel",
        "device",
        "customer_type",
        "app_version",
        "customer_segment",
        "loyalty_tier",
    )
    item_context = (
        tables["order_items"]
        .select("order_item_id", "product_id", "seller_id")
        .join(
            tables["products"].select(
                "product_id",
                "warehouse_id",
                pl.col("category").alias("product_category"),
            ),
            on="product_id",
            how="left",
            validate="m:1",
        )
    )
    refund_context = (
        tables["refunds"]
        .group_by("return_id")
        .agg(
            pl.col("initiated_at").min().alias("refund_initiated_at"),
            pl.col("completed_at").max().alias("refund_completed_at"),
            (pl.col("status") == "completed").any().alias("refund_completed"),
        )
    )
    returns = (
        tables["returns"]
        .join(return_order_context, on="order_id", how="left", validate="m:1")
        .join(item_context, on="order_item_id", how="left", validate="m:1")
        .join(refund_context, on="return_id", how="left", validate="1:1")
        .with_columns(
            pl.col("received_at").is_not_null().alias("return_received"),
            pl.col("refund_completed").fill_null(False),
            (
                (pl.col("refund_completed_at") - pl.col("refund_initiated_at")).dt.total_seconds()
                / 3600.0
            ).alias("refund_processing_hours"),
        )
    )

    sellers = tables["sellers"].select(
        "seller_id",
        pl.col("home_region").alias("region"),
        pl.col("category_focus").alias("product_category"),
    )
    seller_feeds = (
        tables["seller_feed_runs"]
        .join(sellers, on="seller_id", how="left", validate="m:1")
        .with_columns((pl.col("status") == "success").alias("feed_success"))
    )

    pipelines = tables["pipeline_runs"].with_columns(
        (pl.col("status") == "success").alias("pipeline_success"),
        (pl.col("completed_at") - pl.col("started_at"))
        .dt.total_seconds()
        .cast(pl.Float64)
        .alias("pipeline_duration_seconds"),
    )
    deployments = (
        tables["deployments"]
        .with_columns((pl.col("status") == "success").alias("deployment_success"))
        .rename({"region_scope": "deployment_scope"})
    )

    return {
        "checkout": checkout,
        "payments": payments,
        "orders": orders,
        "order_items": order_items,
        "inventory": inventory,
        "shipments": shipments,
        "scans": scans,
        "returns": returns,
        "seller_feeds": seller_feeds,
        "pipelines": pipelines,
        "deployments": deployments,
    }


def expected_fact_cardinalities(tables: FrameMap) -> dict[str, int]:
    """Expected fact row counts for one-to-one analytical reconciliation."""

    return {
        "checkout": tables["checkout_sessions"].height,
        "payments": tables["payment_attempts"].height,
        "orders": tables["orders"].height,
        "order_items": tables["order_items"].height,
        "inventory": tables["inventory_snapshots"].height,
        "shipments": tables["shipments"].height,
        "scans": tables["warehouse_scan_events"].height,
        "returns": tables["returns"].height,
        "seller_feeds": tables["seller_feed_runs"].height,
        "pipelines": tables["pipeline_runs"].height,
        "deployments": tables["deployments"].height,
    }

from __future__ import annotations

import polars as pl

from paic.simulator.io import runtime_manifest
from paic.simulator.manifest import DatasetManifest
from paic.simulator.types import FrameMap, SimulationResult
from paic.simulator.validation import validate_simulation_result, validate_tables


def _replace(result: SimulationResult, table: str, frame: pl.DataFrame) -> FrameMap:
    return {**result.tables, table: frame}


def test_generated_baseline_passes_all_checks(smoke_result: SimulationResult) -> None:
    report = validate_simulation_result(smoke_result)
    assert report.valid
    assert report.issues == ()
    assert 0.45 <= report.statistics["checkout_conversion_rate"] <= 0.95


def test_presence_schema_and_primary_key_failures_are_reported(
    smoke_result: SimulationResult,
) -> None:
    missing = dict(smoke_result.tables)
    missing.pop("customers")
    report = validate_tables(missing)
    assert any(issue.code == "table.missing" for issue in report.issues)

    extra = {**smoke_result.tables, "mystery": pl.DataFrame({"x": [1]})}
    report = validate_tables(extra)
    assert report.valid
    assert any(issue.code == "table.unregistered" for issue in report.issues)

    customers = smoke_result.table("customers")
    duplicate = pl.concat([customers, customers.head(1)])
    report = validate_tables(_replace(smoke_result, "customers", duplicate))
    assert any(issue.code == "primary_key.duplicate" for issue in report.issues)

    missing_column = customers.drop("home_region")
    report = validate_tables(_replace(smoke_result, "customers", missing_column))
    assert any(issue.code == "schema.columns" for issue in report.issues)


def test_foreign_key_and_temporal_failures_are_reported(
    smoke_result: SimulationResult,
) -> None:
    sessions = (
        smoke_result.table("checkout_sessions")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit("CUS-UNKNOWN"))
            .otherwise(pl.col("customer_id"))
            .alias("customer_id")
        )
        .drop("row_number")
    )
    report = validate_tables(_replace(smoke_result, "checkout_sessions", sessions))
    assert any(issue.code == "foreign_key.unmatched" for issue in report.issues)

    sessions = (
        smoke_result.table("checkout_sessions")
        .with_row_index("row_number")
        .with_columns(
            pl.when((pl.col("row_number") == 0) & pl.col("address_submitted_at").is_not_null())
            .then(pl.col("started_at") - pl.duration(seconds=1))
            .otherwise(pl.col("address_submitted_at"))
            .alias("address_submitted_at")
        )
        .drop("row_number")
    )
    report = validate_tables(_replace(smoke_result, "checkout_sessions", sessions))
    assert any(issue.code == "temporal.order" for issue in report.issues)


def test_financial_and_inventory_corruption_is_detected(
    smoke_result: SimulationResult,
) -> None:
    orders = (
        smoke_result.table("orders")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.col("total_amount") + 10.0)
            .otherwise(pl.col("total_amount"))
            .alias("total_amount")
        )
        .drop("row_number")
    )
    report = validate_tables(_replace(smoke_result, "orders", orders))
    codes = {issue.code for issue in report.issues}
    assert "commerce.order_total" in codes
    assert "commerce.payment_amount" in codes

    inventory = (
        smoke_result.table("inventory_snapshots")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.col("available_quantity") + 4)
            .otherwise(pl.col("available_quantity"))
            .alias("available_quantity")
        )
        .drop("row_number")
    )
    report = validate_tables(_replace(smoke_result, "inventory_snapshots", inventory))
    codes = {issue.code for issue in report.issues}
    assert "inventory.balance" in codes
    assert "inventory.feed_delta" in codes


def test_baseline_health_and_window_failures_are_detected(
    smoke_result: SimulationResult,
) -> None:
    sessions = smoke_result.table("checkout_sessions").with_columns(
        pl.lit("checkout_started").alias("stage_reached"),
        pl.lit("customer_abandoned").alias("abandonment_reason"),
        pl.lit(None).cast(pl.Datetime("us", "UTC")).alias("completed_at"),
    )
    empty_orders = smoke_result.table("orders").head(0)
    empty_items = smoke_result.table("order_items").head(0)
    payments = smoke_result.table("payment_attempts").with_columns(pl.lit(False).alias("approved"))
    report = validate_tables(
        {
            **smoke_result.tables,
            "checkout_sessions": sessions,
            "payment_attempts": payments,
            "orders": empty_orders,
            "order_items": empty_items,
        }
    )
    assert any(issue.code == "baseline.conversion" for issue in report.issues)
    assert any(issue.code == "baseline.payment_approval" for issue in report.issues)

    sessions = (
        smoke_result.table("checkout_sessions")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit(smoke_result.config.end_at))
            .otherwise(pl.col("started_at"))
            .alias("started_at")
        )
        .drop("row_number")
    )
    report = validate_tables(
        _replace(smoke_result, "checkout_sessions", sessions), config=smoke_result.config
    )
    assert any(issue.code == "window.after_end" for issue in report.issues)


def test_manifest_rejects_incident_injection(smoke_result: SimulationResult) -> None:
    manifest = DatasetManifest(
        schema_version="1.0",
        simulation_id=smoke_result.config.simulation_id,
        generator_version="0.2.0",
        runtime=runtime_manifest(),
        seed=smoke_result.config.seed,
        config_sha256="0" * 64,
        logical_start_at=smoke_result.config.start_at,
        logical_end_at=smoke_result.config.end_at,
        incident_injections=1,
        tables=[],
    )
    report = validate_tables(smoke_result.tables, manifest=manifest)
    assert any(issue.code == "manifest.incident_injections" for issue in report.issues)


def test_funnel_attribution_and_baseline_rule_corruption_is_detected(
    smoke_result: SimulationResult,
) -> None:
    sessions = (
        smoke_result.table("checkout_sessions")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit("payment_declined"))
            .otherwise(pl.col("abandonment_reason"))
            .alias("abandonment_reason"),
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit("CAM-INVALID-01"))
            .otherwise(pl.col("campaign_id"))
            .alias("campaign_id"),
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit("organic"))
            .otherwise(pl.col("channel"))
            .alias("channel"),
        )
        .drop("row_number")
    )
    payments = (
        smoke_result.table("payment_attempts")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.lit("18"))
            .otherwise(pl.col("fraud_rule_version"))
            .alias("fraud_rule_version")
        )
        .drop("row_number")
    )
    report = validate_tables(
        {
            **smoke_result.tables,
            "checkout_sessions": sessions,
            "payment_attempts": payments,
        }
    )
    codes = {issue.code for issue in report.issues}
    assert "funnel.state" in codes
    assert "commerce.campaign_attribution" in codes
    assert "baseline.fraud_rule" in codes


def test_scan_event_corruption_is_detected(smoke_result: SimulationResult) -> None:
    scans = (
        smoke_result.table("warehouse_scan_events")
        .with_row_index("row_number")
        .with_columns(
            pl.when(pl.col("row_number") == 0)
            .then(pl.col("ingestion_lag_seconds") + 5)
            .otherwise(pl.col("ingestion_lag_seconds"))
            .alias("ingestion_lag_seconds"),
            pl.when(pl.col("row_number") == 1)
            .then(pl.lit("ORD-UNKNOWN"))
            .otherwise(pl.col("order_id"))
            .alias("order_id"),
        )
        .drop("row_number")
    )
    report = validate_tables(_replace(smoke_result, "warehouse_scan_events", scans))
    codes = {issue.code for issue in report.issues}
    assert "fulfilment.scan_lag" in codes
    assert "fulfilment.scan_context" in codes

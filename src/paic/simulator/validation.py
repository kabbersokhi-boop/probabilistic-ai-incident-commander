"""Deterministic schema, integrity, and baseline validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from paic.artifacts.lease import artifact_reader
from paic.simulator.config import SimulationConfig
from paic.simulator.io import (
    DatasetIOError,
    canonical_config_hash,
    file_sha256,
    load_dataset,
)
from paic.simulator.manifest import DatasetManifest, TableManifest
from paic.simulator.schema import TABLE_ORDER, TABLE_SPECS
from paic.simulator.types import FrameMap, SimulationResult


@dataclass(frozen=True)
class DatasetValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    table: str | None
    message: str


@dataclass(frozen=True)
class DatasetValidationReport:
    issues: tuple[DatasetValidationIssue, ...]
    statistics: dict[str, float | int]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [asdict(issue) for issue in self.issues],
            "statistics": self.statistics,
        }


def validate_simulation_result(result: SimulationResult) -> DatasetValidationReport:
    """Validate an in-memory simulation result."""

    return validate_tables(result.tables, config=result.config)


def validate_tables(
    tables: FrameMap,
    *,
    config: SimulationConfig | None = None,
    manifest: DatasetManifest | None = None,
) -> DatasetValidationReport:
    issues: list[DatasetValidationIssue] = []
    statistics: dict[str, float | int] = {}

    _validate_table_presence(tables, issues)
    if any(name not in tables for name in TABLE_ORDER):
        return DatasetValidationReport(tuple(issues), statistics)

    _validate_schemas(tables, issues)
    _validate_keys(tables, issues)
    _validate_foreign_keys(tables, issues)
    _validate_temporal_order(tables, issues)
    _validate_commerce_reconciliation(tables, issues)
    _validate_funnel_states(tables, issues)
    _validate_operational_reconciliation(tables, issues)
    _validate_inventory(tables, issues)
    _validate_baseline_health(tables, issues, statistics)

    if config is not None:
        _validate_window(tables, config, issues)
    if manifest is not None and manifest.incident_injections != 0:
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.incident_injections",
                None,
                "baseline simulator datasets must not contain incident injections",
            )
        )
    return DatasetValidationReport(tuple(issues), statistics)


@artifact_reader
def validate_dataset_directory(dataset_dir: str | Path) -> DatasetValidationReport:
    """Validate an exported dataset, including hashes and its resolved config."""

    root = Path(dataset_dir)
    try:
        manifest, tables = load_dataset(root)
    except DatasetIOError as exc:
        return DatasetValidationReport(
            (
                DatasetValidationIssue(
                    "error",
                    "dataset.load",
                    None,
                    str(exc),
                ),
            ),
            {},
        )

    issues: list[DatasetValidationIssue] = []
    _validate_success_marker(root, manifest, issues)

    config: SimulationConfig | None = None
    config_path = root / "config.resolved.json"
    try:
        config = SimulationConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(
            DatasetValidationIssue(
                "error",
                "dataset.config",
                None,
                f"cannot validate resolved config: {exc}",
            )
        )
    if config is not None:
        _validate_manifest_config(manifest, config, issues)

    manifest_tables = {item.name: item for item in manifest.tables}
    extra_manifest_tables = sorted(set(manifest_tables).difference(TABLE_ORDER))
    for table_name in extra_manifest_tables:
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.table_unregistered",
                table_name,
                "manifest contains a table outside the canonical baseline schema",
            )
        )

    for table_name in TABLE_ORDER:
        table_manifest = manifest_tables.get(table_name)
        if table_manifest is None:
            issues.append(
                DatasetValidationIssue(
                    "error",
                    "manifest.table_missing",
                    table_name,
                    "required table is absent from manifest",
                )
            )
            continue

        path = root / table_manifest.relative_path
        if path.is_file():
            if path.stat().st_size != table_manifest.byte_size:
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "manifest.byte_size",
                        table_name,
                        "Parquet byte size does not match the manifest",
                    )
                )
            if file_sha256(path) != table_manifest.sha256:
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "manifest.hash_mismatch",
                        table_name,
                        "Parquet SHA-256 does not match the manifest",
                    )
                )

        frame = tables.get(table_name)
        if frame is not None:
            if frame.height != table_manifest.row_count:
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "manifest.row_count",
                        table_name,
                        "Parquet row count does not match the manifest",
                    )
                )
            _validate_table_manifest(table_name, frame, table_manifest, issues)

    table_report = validate_tables(tables, config=config, manifest=manifest)
    return DatasetValidationReport(tuple([*issues, *table_report.issues]), table_report.statistics)


def _validate_success_marker(
    root: Path,
    manifest: DatasetManifest,
    issues: list[DatasetValidationIssue],
) -> None:
    marker = root / "_SUCCESS"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        issues.append(
            DatasetValidationIssue(
                "error",
                "dataset.success_marker",
                None,
                f"cannot read success marker: {exc}",
            )
        )
        return
    if value != manifest.config_sha256:
        issues.append(
            DatasetValidationIssue(
                "error",
                "dataset.success_marker",
                None,
                "success marker does not match the manifest configuration hash",
            )
        )


def _validate_manifest_config(
    manifest: DatasetManifest,
    config: SimulationConfig,
    issues: list[DatasetValidationIssue],
) -> None:
    comparisons = [
        (
            canonical_config_hash(config) == manifest.config_sha256,
            "dataset.config_hash",
            "resolved config does not match the manifest hash",
        ),
        (
            config.simulation_id == manifest.simulation_id,
            "manifest.simulation_id",
            "manifest simulation_id does not match the resolved config",
        ),
        (
            config.seed == manifest.seed,
            "manifest.seed",
            "manifest seed does not match the resolved config",
        ),
        (
            config.start_at == manifest.logical_start_at,
            "manifest.logical_start",
            "manifest logical_start_at does not match the resolved config",
        ),
        (
            config.end_at == manifest.logical_end_at,
            "manifest.logical_end",
            "manifest logical_end_at does not match the resolved config",
        ),
    ]
    for valid, code, message in comparisons:
        if not valid:
            issues.append(DatasetValidationIssue("error", code, None, message))


def _validate_table_manifest(
    table_name: str,
    frame: pl.DataFrame,
    manifest: TableManifest,
    issues: list[DatasetValidationIssue],
) -> None:
    spec = TABLE_SPECS[table_name]
    if manifest.primary_key != list(spec.primary_key):
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.primary_key",
                table_name,
                "manifest primary key does not match the canonical schema",
            )
        )

    expected_foreign_keys = [
        (item.column, item.target_table, item.target_column, item.nullable)
        for item in spec.foreign_keys
    ]
    actual_foreign_keys = [
        (item.column, item.target_table, item.target_column, item.nullable)
        for item in manifest.foreign_keys
    ]
    if actual_foreign_keys != expected_foreign_keys:
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.foreign_keys",
                table_name,
                "manifest foreign keys do not match the canonical schema",
            )
        )

    expected_columns = [(name, str(dtype)) for name, dtype in spec.columns]
    actual_columns = [(item.name, item.dtype) for item in manifest.columns]
    if actual_columns != expected_columns:
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.columns",
                table_name,
                "manifest columns do not match the canonical schema",
            )
        )

    timestamp_values: list[Any] = []
    for column in spec.timestamp_columns:
        series = frame.get_column(column).drop_nulls()
        if not series.is_empty():
            timestamp_values.extend([series.min(), series.max()])
    non_null_timestamps = [value for value in timestamp_values if value is not None]
    minimum = min(non_null_timestamps) if non_null_timestamps else None
    maximum = max(non_null_timestamps) if non_null_timestamps else None
    if manifest.minimum_timestamp != minimum or manifest.maximum_timestamp != maximum:
        issues.append(
            DatasetValidationIssue(
                "error",
                "manifest.timestamp_range",
                table_name,
                "manifest timestamp range does not match the Parquet table",
            )
        )


def report_to_json(report: DatasetValidationReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)


def _validate_table_presence(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    for name in TABLE_ORDER:
        if name not in tables:
            issues.append(
                DatasetValidationIssue(
                    "error", "table.missing", name, "required simulation table is missing"
                )
            )
    for name in sorted(set(tables).difference(TABLE_ORDER)):
        issues.append(
            DatasetValidationIssue(
                "warning", "table.unregistered", name, "table is not in the schema registry"
            )
        )


def _validate_schemas(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    for table_name, spec in TABLE_SPECS.items():
        frame = tables[table_name]
        expected_columns = [name for name, _ in spec.columns]
        if frame.columns != expected_columns:
            issues.append(
                DatasetValidationIssue(
                    "error",
                    "schema.columns",
                    table_name,
                    f"expected columns {expected_columns}, found {frame.columns}",
                )
            )
            continue
        for column, expected_dtype in spec.columns:
            actual_dtype = frame.schema[column]
            if actual_dtype != expected_dtype:
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "schema.dtype",
                        table_name,
                        f"{column} expected {expected_dtype}, found {actual_dtype}",
                    )
                )


def _validate_keys(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    for table_name, spec in TABLE_SPECS.items():
        frame = tables[table_name]
        if frame.is_empty():
            if table_name not in {"promotions", "returns", "refunds"}:
                issues.append(
                    DatasetValidationIssue(
                        "error", "table.empty", table_name, "required baseline table is empty"
                    )
                )
            continue
        if any(frame.get_column(column).null_count() for column in spec.primary_key):
            issues.append(
                DatasetValidationIssue(
                    "error", "primary_key.null", table_name, "primary key contains null values"
                )
            )
        unique_count = frame.select(pl.struct(list(spec.primary_key)).n_unique()).item()
        if int(unique_count) != frame.height:
            issues.append(
                DatasetValidationIssue(
                    "error", "primary_key.duplicate", table_name, "primary key is not unique"
                )
            )


def _validate_foreign_keys(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    for table_name, spec in TABLE_SPECS.items():
        frame = tables[table_name]
        for foreign_key in spec.foreign_keys:
            source = frame.select(foreign_key.column)
            if foreign_key.nullable:
                source = source.filter(pl.col(foreign_key.column).is_not_null())
            elif source.get_column(foreign_key.column).null_count():
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "foreign_key.null",
                        table_name,
                        f"{foreign_key.column} contains null values",
                    )
                )
            target = tables[foreign_key.target_table].select(
                pl.col(foreign_key.target_column).alias(foreign_key.column)
            )
            unmatched = source.unique().join(target.unique(), on=foreign_key.column, how="anti")
            if not unmatched.is_empty():
                issues.append(
                    DatasetValidationIssue(
                        "error",
                        "foreign_key.unmatched",
                        table_name,
                        f"{foreign_key.column} has {unmatched.height} unmatched values",
                    )
                )


def _validate_temporal_order(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    checks = [
        ("checkout_sessions", "started_at", "address_submitted_at"),
        ("checkout_sessions", "address_submitted_at", "inventory_checked_at"),
        ("checkout_sessions", "inventory_checked_at", "payment_started_at"),
        ("checkout_sessions", "payment_started_at", "completed_at"),
        ("promotions", "start_at", "end_at"),
        ("shipments", "shipped_at", "delivered_at"),
        ("warehouse_scan_events", "source_event_at", "platform_received_at"),
        ("returns", "requested_at", "received_at"),
        ("refunds", "initiated_at", "completed_at"),
        ("seller_feed_runs", "started_at", "completed_at"),
        ("pipeline_runs", "started_at", "completed_at"),
    ]
    for table_name, earlier, later in checks:
        frame = tables[table_name]
        if frame.is_empty():
            continue
        invalid = frame.filter(
            pl.col(earlier).is_not_null()
            & pl.col(later).is_not_null()
            & (pl.col(earlier) > pl.col(later))
        )
        if not invalid.is_empty():
            issues.append(
                DatasetValidationIssue(
                    "error",
                    "temporal.order",
                    table_name,
                    f"{invalid.height} rows have {earlier} after {later}",
                )
            )


def _validate_commerce_reconciliation(
    tables: FrameMap, issues: list[DatasetValidationIssue]
) -> None:
    sessions = tables["checkout_sessions"]
    payments = tables["payment_attempts"]
    orders = tables["orders"]
    items = tables["order_items"]

    approved = payments.filter(pl.col("approved"))
    if approved.height != orders.height:
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.approved_order_count",
                "orders",
                "approved payment count must equal order count",
            )
        )
    completed = sessions.filter(pl.col("stage_reached") == "order_completed")
    if completed.height != orders.height:
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.completed_order_count",
                "checkout_sessions",
                "completed session count must equal order count",
            )
        )

    item_summary = items.group_by("order_id").agg(
        pl.len().alias("actual_item_count"),
        pl.col("line_total").sum().alias("item_net_total"),
    )
    reconciled = orders.join(item_summary, on="order_id", how="left")
    bad_item_count = reconciled.filter(pl.col("item_count") != pl.col("actual_item_count"))
    if not bad_item_count.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.item_count",
                "orders",
                "order item_count does not match item rows",
            )
        )
    bad_net = reconciled.filter(
        (pl.col("item_net_total") - (pl.col("subtotal") - pl.col("discount_amount"))).abs() > 0.02
    )
    if not bad_net.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.item_total",
                "orders",
                "order net subtotal does not reconcile to item rows",
            )
        )
    bad_total = orders.filter(
        (
            pl.col("total_amount")
            - (
                pl.col("subtotal")
                - pl.col("discount_amount")
                + pl.col("tax_amount")
                + pl.col("shipping_amount")
            )
        ).abs()
        > 0.02
    )
    if not bad_total.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.order_total",
                "orders",
                "order total does not reconcile",
            )
        )

    payment_orders = orders.join(
        payments.select("payment_attempt_id", pl.col("amount").alias("payment_amount")),
        on="payment_attempt_id",
    )
    bad_payment = payment_orders.filter(
        (pl.col("payment_amount") - pl.col("total_amount")).abs() > 0.02
    )
    if not bad_payment.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.payment_amount",
                "payment_attempts",
                "approved payment amount does not equal order total",
            )
        )


def _validate_funnel_states(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    sessions = tables["checkout_sessions"]
    expectations: dict[str, tuple[str | None, tuple[str, ...], tuple[str, ...]]] = {
        "checkout_started": (
            "customer_abandoned",
            (),
            ("address_submitted_at", "inventory_checked_at", "payment_started_at", "completed_at"),
        ),
        "address_submitted": (
            "address_validation_failed",
            ("address_submitted_at",),
            ("inventory_checked_at", "payment_started_at", "completed_at"),
        ),
        "inventory_checked": (
            "inventory_unavailable",
            ("address_submitted_at", "inventory_checked_at"),
            ("payment_started_at", "completed_at"),
        ),
        "inventory_confirmed": (
            "before_payment",
            ("address_submitted_at", "inventory_checked_at"),
            ("payment_started_at", "completed_at"),
        ),
        "payment_started": (
            "payment_declined",
            ("address_submitted_at", "inventory_checked_at", "payment_started_at"),
            ("completed_at",),
        ),
        "order_completed": (
            None,
            ("address_submitted_at", "inventory_checked_at", "payment_started_at", "completed_at"),
            (),
        ),
    }
    observed_stages = set(sessions.get_column("stage_reached").unique().to_list())
    unknown = sorted(observed_stages.difference(expectations))
    if unknown:
        issues.append(
            DatasetValidationIssue(
                "error",
                "funnel.stage_unknown",
                "checkout_sessions",
                f"unknown funnel stages: {', '.join(str(item) for item in unknown)}",
            )
        )

    for stage, (reason, required, forbidden) in expectations.items():
        cohort = sessions.filter(pl.col("stage_reached") == stage)
        if cohort.is_empty():
            continue
        invalid = pl.lit(False)
        for column in required:
            invalid = invalid | pl.col(column).is_null()
        for column in forbidden:
            invalid = invalid | pl.col(column).is_not_null()
        if reason is None:
            invalid = invalid | pl.col("abandonment_reason").is_not_null()
        else:
            invalid = invalid | (pl.col("abandonment_reason") != reason)
        count = cohort.filter(invalid).height
        if count:
            issues.append(
                DatasetValidationIssue(
                    "error",
                    "funnel.state",
                    "checkout_sessions",
                    f"{count} {stage} rows have inconsistent timestamps or abandonment reason",
                )
            )

    unattributed = sessions.filter(
        pl.col("channel").is_in(["organic", "direct"]) & pl.col("campaign_id").is_not_null()
    )
    paid_without_campaign = sessions.filter(
        ~pl.col("channel").is_in(["organic", "direct"]) & pl.col("campaign_id").is_null()
    )
    if not unattributed.is_empty() or not paid_without_campaign.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.campaign_attribution",
                "checkout_sessions",
                "campaign attribution does not match organic/direct versus campaign channels",
            )
        )


def _validate_operational_reconciliation(
    tables: FrameMap, issues: list[DatasetValidationIssue]
) -> None:
    shipments = tables["shipments"]
    scans = tables["warehouse_scan_events"]
    if scans.is_empty() and not shipments.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "fulfilment.scan_events_missing",
                "warehouse_scan_events",
                "shipments exist without warehouse scan events",
            )
        )
        return

    scan_summary = scans.group_by("shipment_id").agg(
        pl.len().alias("actual_scan_count"),
        pl.col("sequence_number").min().alias("minimum_sequence"),
        pl.col("sequence_number").max().alias("maximum_sequence"),
    )
    reconciled = shipments.join(scan_summary, on="shipment_id", how="left")
    invalid_count = reconciled.filter(
        pl.col("actual_scan_count").is_null()
        | (pl.col("scan_count") != pl.col("actual_scan_count"))
        | (pl.col("minimum_sequence") != 1)
        | (pl.col("maximum_sequence") != pl.col("actual_scan_count"))
    )
    if not invalid_count.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "fulfilment.scan_count",
                "shipments",
                "shipment scan_count or scan sequence does not reconcile to scan events",
            )
        )

    expected_lag = (pl.col("platform_received_at") - pl.col("source_event_at")).dt.total_seconds()
    invalid_lag = scans.filter(
        (pl.col("ingestion_lag_seconds") != expected_lag)
        | (pl.col("ingestion_lag_seconds") < pl.col("connector_batch_seconds"))
        | (pl.col("ingestion_lag_seconds") <= 0)
    )
    if not invalid_lag.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "fulfilment.scan_lag",
                "warehouse_scan_events",
                "scan receipt timestamps or ingestion lag fields do not reconcile",
            )
        )

    shipment_context = shipments.select(
        "shipment_id",
        pl.col("order_id").alias("expected_order_id"),
        pl.col("warehouse_id").alias("expected_warehouse_id"),
    )
    inconsistent_context = scans.join(shipment_context, on="shipment_id").filter(
        (pl.col("order_id") != pl.col("expected_order_id"))
        | (pl.col("warehouse_id") != pl.col("expected_warehouse_id"))
    )
    if not inconsistent_context.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "fulfilment.scan_context",
                "warehouse_scan_events",
                "scan event order or warehouse does not match its shipment",
            )
        )

    session_attribution = tables["checkout_sessions"].select(
        pl.col("session_id"),
        pl.col("channel").alias("session_channel"),
        pl.col("campaign_id").alias("session_campaign_id"),
    )
    attribution = (
        tables["orders"]
        .select("order_id", "session_id", "channel", "campaign_id")
        .join(session_attribution, on="session_id")
    )
    invalid_attribution = attribution.filter(
        (pl.col("channel") != pl.col("session_channel"))
        | (pl.col("campaign_id").fill_null("") != pl.col("session_campaign_id").fill_null(""))
    )
    if not invalid_attribution.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "commerce.order_attribution",
                "orders",
                "order channel or campaign does not match the originating checkout session",
            )
        )


def _validate_inventory(tables: FrameMap, issues: list[DatasetValidationIssue]) -> None:
    inventory = tables["inventory_snapshots"]
    invalid = inventory.filter(
        (pl.col("available_quantity") != pl.col("on_hand_quantity") - pl.col("reserved_quantity"))
        | (pl.col("on_hand_quantity") < 0)
        | (pl.col("reserved_quantity") < 0)
        | (pl.col("available_quantity") < 0)
    )
    if not invalid.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "inventory.balance",
                "inventory_snapshots",
                "inventory quantities do not reconcile or contain negatives",
            )
        )
    feed_delta = inventory.select(
        (pl.col("feed_reported_quantity") - pl.col("available_quantity")).abs().max()
    ).item()
    if int(feed_delta or 0) > 1:
        issues.append(
            DatasetValidationIssue(
                "error",
                "inventory.feed_delta",
                "inventory_snapshots",
                "incident-free feed quantity differs from availability by more than one unit",
            )
        )


def _validate_baseline_health(
    tables: FrameMap,
    issues: list[DatasetValidationIssue],
    statistics: dict[str, float | int],
) -> None:
    sessions = tables["checkout_sessions"]
    payments = tables["payment_attempts"]
    orders = tables["orders"]
    delivered = tables["shipments"].filter(pl.col("status") == "delivered")

    conversion = orders.height / sessions.height if sessions.height else 0.0
    approval = (
        payments.filter(pl.col("approved")).height / payments.height if payments.height else 0.0
    )
    late_rate = (
        delivered.filter(pl.col("late")).height / delivered.height if delivered.height else 0.0
    )
    statistics.update(
        {
            "checkout_conversion_rate": conversion,
            "payment_approval_rate": approval,
            "late_delivery_rate": late_rate,
            "session_count": sessions.height,
            "order_count": orders.height,
        }
    )
    if not 0.45 <= conversion <= 0.95:
        issues.append(
            DatasetValidationIssue(
                "error",
                "baseline.conversion",
                "checkout_sessions",
                f"checkout conversion {conversion:.3f} is outside healthy baseline bounds",
            )
        )
    if not 0.70 <= approval <= 0.995:
        issues.append(
            DatasetValidationIssue(
                "error",
                "baseline.payment_approval",
                "payment_attempts",
                f"payment approval {approval:.3f} is outside healthy baseline bounds",
            )
        )
    non_baseline_rules = payments.filter(pl.col("fraud_rule_version") != "17")
    if not non_baseline_rules.is_empty():
        issues.append(
            DatasetValidationIssue(
                "error",
                "baseline.fraud_rule",
                "payment_attempts",
                "incident-free baseline must use fraud rule version 17",
            )
        )

    if late_rate > 0.30:
        issues.append(
            DatasetValidationIssue(
                "error",
                "baseline.late_delivery",
                "shipments",
                f"late delivery rate {late_rate:.3f} is outside healthy baseline bounds",
            )
        )


def _validate_window(
    tables: FrameMap,
    config: SimulationConfig,
    issues: list[DatasetValidationIssue],
) -> None:
    starts = tables["checkout_sessions"].get_column("started_at")
    if starts.is_empty():
        return
    minimum = starts.min()
    maximum = starts.max()
    if minimum is not None and minimum < config.start_at:
        issues.append(
            DatasetValidationIssue(
                "error", "window.before_start", "checkout_sessions", "session predates start_at"
            )
        )
    if maximum is not None and maximum >= config.end_at:
        issues.append(
            DatasetValidationIssue(
                "error", "window.after_end", "checkout_sessions", "session is at or after end_at"
            )
        )

"""Human- and machine-readable profiles for synthetic datasets."""

from __future__ import annotations

from typing import Any

import polars as pl

from paic.simulator.types import FrameMap


def build_profile(tables: FrameMap) -> dict[str, Any]:
    """Calculate a compact business profile without implementing the metric layer."""

    sessions = tables["checkout_sessions"]
    payments = tables["payment_attempts"]
    orders = tables["orders"]
    shipments = tables["shipments"]
    returns = tables["returns"]
    scan_events = tables["warehouse_scan_events"]

    session_count = sessions.height
    payment_count = payments.height
    order_count = orders.height
    delivered = shipments.filter(pl.col("status") == "delivered")
    delivered_count = delivered.height

    revenue = float(orders.get_column("total_amount").sum() or 0.0)
    return {
        "table_row_counts": {name: frame.height for name, frame in tables.items()},
        "business": {
            "checkout_sessions": session_count,
            "payment_attempts": payment_count,
            "orders": order_count,
            "gross_order_value": round(revenue, 2),
            "checkout_conversion_rate": round(order_count / session_count, 6)
            if session_count
            else 0.0,
            "payment_approval_rate": round(
                payments.filter(pl.col("approved")).height / payment_count, 6
            )
            if payment_count
            else 0.0,
            "delivered_shipments": delivered_count,
            "late_delivery_rate": round(
                delivered.filter(pl.col("late")).height / delivered_count, 6
            )
            if delivered_count
            else 0.0,
            "return_rate_per_order": round(returns.height / order_count, 6) if order_count else 0.0,
            "warehouse_scan_events": scan_events.height,
            "median_scan_ingestion_lag_seconds": _quantile(
                scan_events, "ingestion_lag_seconds", 0.5
            ),
            "p95_scan_ingestion_lag_seconds": _quantile(scan_events, "ingestion_lag_seconds", 0.95),
        },
        "cohorts": {
            "sessions_by_region": _count_by(sessions, "region"),
            "sessions_by_device": _count_by(sessions, "device"),
            "sessions_by_customer_type": _count_by(sessions, "customer_type"),
            "orders_by_region": _count_by(orders, "region"),
            "orders_by_channel": _count_by(orders, "channel"),
        },
    }


def _count_by(frame: pl.DataFrame, column: str) -> dict[str, int]:
    if frame.is_empty():
        return {}
    rows = frame.group_by(column).len().sort(column).iter_rows()
    return {str(value): int(count) for value, count in rows}


def _quantile(frame: pl.DataFrame, column: str, probability: float) -> float:
    if frame.is_empty():
        return 0.0
    value = frame.get_column(column).quantile(probability, interpolation="nearest")
    return round(float(value or 0.0), 6)

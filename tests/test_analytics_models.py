from __future__ import annotations

import polars as pl

from paic.analytics.models import build_facts, expected_fact_cardinalities
from paic.simulator.types import SimulationResult


def test_fact_models_preserve_source_cardinality_and_primary_keys(
    smoke_result: SimulationResult,
) -> None:
    facts = build_facts(smoke_result.tables)
    expected = expected_fact_cardinalities(smoke_result.tables)
    key_columns = {
        "checkout": ["session_id"],
        "payments": ["payment_attempt_id"],
        "orders": ["order_id"],
        "order_items": ["order_item_id"],
        "inventory": ["snapshot_id"],
        "shipments": ["shipment_id"],
        "scans": ["scan_event_id"],
        "returns": ["return_id"],
        "seller_feeds": ["feed_run_id"],
        "pipelines": ["pipeline_run_id"],
        "deployments": ["deployment_id"],
    }

    assert set(facts) == set(expected)
    for name, expected_rows in expected.items():
        assert facts[name].height == expected_rows
        assert not facts[name].select(key_columns[name]).is_duplicated().any()


def test_checkout_and_payment_derived_fields_reconcile(
    smoke_result: SimulationResult,
) -> None:
    facts = build_facts(smoke_result.tables)
    checkout = facts["checkout"]
    payments = facts["payments"]

    assert checkout.get_column("has_order").sum() == smoke_result.tables["orders"].height
    assert (
        checkout.get_column("address_submitted").sum()
        == checkout.get_column("address_submitted_at").is_not_null().sum()
    )
    assert checkout.get_column("payment_started").sum() == payments.height
    assert (
        payments.get_column("declined").sum() + payments.get_column("approved").sum()
        == payments.height
    )
    risk_rejections = payments.filter(pl.col("risk_rejected"))
    assert risk_rejections.filter(pl.col("decline_reason") != "risk_rejected").is_empty()


def test_joined_business_dimensions_are_complete(smoke_result: SimulationResult) -> None:
    facts = build_facts(smoke_result.tables)
    required = {
        "checkout": [
            "region",
            "device",
            "channel",
            "customer_segment",
            "loyalty_tier",
            "product_category",
            "seller_id",
            "warehouse_id",
        ],
        "payments": ["region", "device", "issuer", "payment_method", "processor"],
        "orders": ["region", "device", "customer_segment", "loyalty_tier"],
        "inventory": ["region", "product_category", "seller_id", "warehouse_id"],
        "shipments": ["region", "device", "warehouse_id"],
        "seller_feeds": ["region", "product_category", "seller_id"],
        "pipelines": ["pipeline_name"],
        "deployments": ["service", "deployment_scope"],
    }
    for fact_name, columns in required.items():
        for column in columns:
            assert facts[fact_name].get_column(column).null_count() == 0


def test_inventory_fulfilment_and_pipeline_features_are_consistent(
    smoke_result: SimulationResult,
) -> None:
    facts = build_facts(smoke_result.tables)
    inventory = facts["inventory"]
    shipments = facts["shipments"]
    pipelines = facts["pipelines"]

    expected_gap = (
        (
            inventory.get_column("feed_reported_quantity")
            - inventory.get_column("available_quantity")
        )
        .abs()
        .cast(pl.Float64)
    )
    assert inventory.get_column("inventory_gap_units").equals(expected_gap)
    assert inventory.get_column("inventory_exact_match").equals(expected_gap == 0)
    assert (
        shipments.filter(pl.col("delivered")).get_column("delivery_duration_hours").null_count()
        == 0
    )
    assert pipelines.get_column("pipeline_duration_seconds").min() >= 0

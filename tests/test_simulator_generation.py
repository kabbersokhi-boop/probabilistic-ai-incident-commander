from __future__ import annotations

import polars as pl

from paic.simulator.engine import simulate
from paic.simulator.profile import build_profile
from paic.simulator.schema import TABLE_ORDER, TABLE_SPECS, conform_frame, empty_frame
from paic.simulator.types import SimulationResult


def test_generator_produces_complete_relational_environment(
    smoke_result: SimulationResult,
) -> None:
    assert tuple(smoke_result.tables) == TABLE_ORDER
    assert len(smoke_result.tables) == 17
    assert smoke_result.table("customers").height == smoke_result.config.scale.customers
    assert smoke_result.table("products").height == smoke_result.config.scale.products
    assert smoke_result.table("checkout_sessions").height > 100
    assert smoke_result.table("orders").height > 50
    assert smoke_result.table("inventory_snapshots").height == (
        smoke_result.config.scale.products * smoke_result.config.duration_days
    )
    assert not any(
        {"incident_id", "root_cause", "injected_incident"}.intersection(frame.columns)
        for frame in smoke_result.tables.values()
    )


def test_all_frames_match_the_canonical_schema(smoke_result: SimulationResult) -> None:
    for table_name, frame in smoke_result.tables.items():
        spec = TABLE_SPECS[table_name]
        assert frame.columns == [name for name, _ in spec.columns]
        assert frame.schema == spec.schema


def test_generation_is_reproducible_and_seed_sensitive(
    smoke_result: SimulationResult,
) -> None:
    repeated = simulate(smoke_result.config)
    for table_name in TABLE_ORDER:
        assert repeated.table(table_name).equals(smoke_result.table(table_name))

    changed = simulate(
        smoke_result.config.model_copy(
            update={"seed": smoke_result.config.seed + 1, "simulation_id": "different-seed"}
        )
    )
    assert not changed.table("customers").equals(smoke_result.table("customers"))
    assert changed.table("customers").height == smoke_result.table("customers").height


def test_commerce_records_reconcile(smoke_result: SimulationResult) -> None:
    tables = smoke_result.tables
    sessions = tables["checkout_sessions"]
    payments = tables["payment_attempts"]
    orders = tables["orders"]
    items = tables["order_items"]

    assert sessions.filter(pl.col("stage_reached") == "order_completed").height == orders.height
    assert payments.filter(pl.col("approved")).height == orders.height
    assert orders.get_column("session_id").n_unique() == orders.height
    assert items.get_column("order_id").n_unique() == orders.height

    item_totals = items.group_by("order_id").agg(
        pl.col("line_total").sum().alias("item_total"), pl.len().alias("item_rows")
    )
    reconciled = orders.join(item_totals, on="order_id")
    assert reconciled.filter(
        (pl.col("item_total") - (pl.col("subtotal") - pl.col("discount_amount"))).abs() > 0.02
    ).is_empty()
    assert reconciled.filter(pl.col("item_rows") != pl.col("item_count")).is_empty()


def test_inventory_and_operational_baseline_are_coherent(
    smoke_result: SimulationResult,
) -> None:
    inventory = smoke_result.table("inventory_snapshots")
    assert inventory.filter(
        pl.col("available_quantity") != pl.col("on_hand_quantity") - pl.col("reserved_quantity")
    ).is_empty()
    assert (
        inventory.select(
            (pl.col("feed_reported_quantity") - pl.col("available_quantity")).abs().max()
        ).item()
        <= 1
    )

    feeds = smoke_result.table("seller_feed_runs")
    pipelines = smoke_result.table("pipeline_runs")
    deployments = smoke_result.table("deployments")
    assert feeds.height == smoke_result.config.scale.sellers * smoke_result.config.duration_days
    assert (
        pipelines.height == len(smoke_result.config.pipelines) * smoke_result.config.duration_days
    )
    assert deployments.get_column("rollback_available").all()


def test_richer_window_generates_returns_and_refunds(rich_result: SimulationResult) -> None:
    assert rich_result.table("returns").height > 0
    assert rich_result.table("refunds").height == rich_result.table("returns").height
    assert rich_result.table("shipments").filter(pl.col("status") == "delivered").height > 0


def test_profile_exposes_business_and_cohort_summary(
    smoke_result: SimulationResult,
) -> None:
    profile = build_profile(smoke_result.tables)
    business = profile["business"]
    assert business["checkout_sessions"] == smoke_result.table("checkout_sessions").height
    assert business["orders"] == smoke_result.table("orders").height
    assert 0 < business["checkout_conversion_rate"] < 1
    assert set(profile["cohorts"]["sessions_by_region"]) == {
        region.code for region in smoke_result.config.regions
    }


def test_empty_frame_and_unknown_table_behaviour(smoke_result: SimulationResult) -> None:
    assert empty_frame("returns").schema == TABLE_SPECS["returns"].schema
    with pl.Config(tbl_rows=2):
        frame = conform_frame("returns", empty_frame("returns"))
        assert frame.is_empty()
    try:
        smoke_result.table("does-not-exist")
    except KeyError as exc:
        assert "unknown simulation table" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unknown table did not raise")


def test_seed_entities_match_incident_contract_vocabulary(
    smoke_result: SimulationResult,
) -> None:
    sellers = smoke_result.table("sellers").filter(
        pl.col("seller_id").is_in(["S-101", "S-204", "S-305"])
    )
    profiles = {
        row["seller_id"]: (
            row["home_region"],
            row["category_focus"],
            row["feed_schema_version"],
        )
        for row in sellers.iter_rows(named=True)
    }
    assert profiles == {
        "S-101": ("IN-NORTH", "home", "v2"),
        "S-204": ("IN-SOUTH", "consumer-electronics", "v2"),
        "S-305": ("IN-WEST", "apparel", "v2"),
    }

    warehouses = {
        row["warehouse_id"]: row["region"]
        for row in smoke_result.table("warehouses").iter_rows(named=True)
    }
    assert warehouses == {
        "W-12": "IN-NORTH",
        "W-17": "IN-WEST",
        "W-22": "IN-SOUTH",
        "W-31": "IN-EAST",
    }
    assert {"BANK-A", "BANK-B", "BANK-C"}.issubset(
        set(smoke_result.table("customers").get_column("issuer").unique())
    )


def test_funnel_state_and_campaign_attribution_are_semantically_consistent(
    smoke_result: SimulationResult,
) -> None:
    sessions = smoke_result.table("checkout_sessions")
    assert set(sessions.get_column("customer_type").unique()) == {"new", "returning"}
    assert sessions.filter(
        pl.col("channel").is_in(["organic", "direct"]) & pl.col("campaign_id").is_not_null()
    ).is_empty()
    assert sessions.filter(
        ~pl.col("channel").is_in(["organic", "direct"]) & pl.col("campaign_id").is_null()
    ).is_empty()

    address_failures = sessions.filter(pl.col("stage_reached") == "address_submitted")
    assert address_failures.filter(pl.col("address_submitted_at").is_null()).is_empty()
    assert address_failures.filter(
        pl.col("abandonment_reason") != "address_validation_failed"
    ).is_empty()

    inventory_failures = sessions.filter(pl.col("stage_reached") == "inventory_checked")
    assert inventory_failures.filter(pl.col("inventory_checked_at").is_null()).is_empty()
    assert inventory_failures.filter(
        pl.col("abandonment_reason") != "inventory_unavailable"
    ).is_empty()

    assert smoke_result.table("payment_attempts").get_column(
        "fraud_rule_version"
    ).unique().to_list() == ["17"]


def test_fulfilment_scan_events_reconcile_with_shipments(
    smoke_result: SimulationResult,
) -> None:
    shipments = smoke_result.table("shipments")
    scans = smoke_result.table("warehouse_scan_events")
    assert set(shipments.get_column("delivery_service").unique()).issubset({"standard", "express"})
    summary = scans.group_by("shipment_id").agg(
        pl.len().alias("events"),
        pl.col("sequence_number").min().alias("minimum_sequence"),
        pl.col("sequence_number").max().alias("maximum_sequence"),
    )
    reconciled = shipments.join(summary, on="shipment_id")
    assert reconciled.filter(pl.col("scan_count") != pl.col("events")).is_empty()
    assert reconciled.filter(pl.col("minimum_sequence") != 1).is_empty()
    assert reconciled.filter(pl.col("maximum_sequence") != pl.col("events")).is_empty()
    assert scans.filter(pl.col("platform_received_at") <= pl.col("source_event_at")).is_empty()
    assert scans.filter(
        pl.col("ingestion_lag_seconds") < pl.col("connector_batch_seconds")
    ).is_empty()

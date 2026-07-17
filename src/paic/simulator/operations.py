"""Generate fulfilment, inventory, seller-feed, pipeline, and deployment data."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import numpy.typing as npt
import polars as pl

from paic.simulator.config import SimulationConfig
from paic.simulator.randomness import RandomFactory
from paic.simulator.schema import conform_frame, empty_frame
from paic.simulator.types import FrameMap
from paic.simulator.utils import probability_vector


def generate_operational_data(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    commerce: FrameMap,
) -> FrameMap:
    """Generate normal operational records and update order fulfilment state."""

    inventory = _generate_inventory(config, random, dimensions, commerce)
    shipments, orders = _generate_shipments(config, random, dimensions, commerce)
    scan_events, shipments = _generate_scan_events(random, shipments)
    returns, refunds = _generate_returns_and_refunds(
        config,
        random,
        dimensions,
        {**commerce, "orders": orders, "shipments": shipments},
    )
    seller_feeds = _generate_seller_feed_runs(config, random, dimensions)
    pipeline_runs = _generate_pipeline_runs(config, random, commerce)
    deployments = _generate_deployments(config, random)
    return {
        "orders": orders,
        "inventory_snapshots": inventory,
        "shipments": shipments,
        "warehouse_scan_events": scan_events,
        "returns": returns,
        "refunds": refunds,
        "seller_feed_runs": seller_feeds,
        "pipeline_runs": pipeline_runs,
        "deployments": deployments,
    }


def _generate_inventory(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    commerce: FrameMap,
) -> pl.DataFrame:
    rng = random.numpy("operations.inventory")
    products = dimensions["products"]
    product_ids = products.get_column("product_id").to_list()
    warehouse_ids = products.get_column("warehouse_id").to_list()
    reorder_points = products.get_column("reorder_point").to_numpy().astype(np.int64)
    product_index = {value: index for index, value in enumerate(product_ids)}

    demand: dict[tuple[date, int], int] = defaultdict(int)
    order_items = commerce["order_items"]
    orders = commerce["orders"].select("order_id", "ordered_at")
    demand_rows = order_items.join(orders, on="order_id", how="inner").select(
        "product_id", "quantity", "ordered_at"
    )
    for row in demand_rows.iter_rows(named=True):
        day = row["ordered_at"].date()
        demand[(day, product_index[str(row["product_id"])])] += int(row["quantity"])

    count = len(product_ids)
    on_hand = reorder_points * rng.integers(3, 7, size=count) + rng.integers(10, 100, size=count)
    rows: list[dict[str, object]] = []
    next_id = 1
    for day_offset in range(config.duration_days):
        snapshot_at = config.start_at + timedelta(days=day_offset, hours=23, minutes=55)
        current_day = snapshot_at.date()
        daily_demand: npt.NDArray[np.int64] = np.asarray(
            [demand.get((current_day, index), 0) for index in range(count)], dtype=np.int64
        )
        on_hand = np.maximum(on_hand - daily_demand, 0)
        restock_mask = on_hand < reorder_points
        restock = np.where(
            restock_mask,
            reorder_points * rng.integers(3, 6, size=count),
            0,
        )
        on_hand += restock
        reserved = np.minimum(rng.poisson(1.2, size=count), on_hand).astype(np.int64)
        available = on_hand - reserved
        feed_noise = rng.choice([-1, 0, 0, 0, 0, 1], size=count)
        reported = np.maximum(available + feed_noise, 0)

        for index in range(count):
            rows.append(
                {
                    "snapshot_id": f"INV-{next_id:012d}",
                    "product_id": product_ids[index],
                    "warehouse_id": warehouse_ids[index],
                    "snapshot_at": snapshot_at,
                    "on_hand_quantity": int(on_hand[index]),
                    "reserved_quantity": int(reserved[index]),
                    "available_quantity": int(available[index]),
                    "reorder_point": int(reorder_points[index]),
                    "feed_reported_quantity": int(reported[index]),
                }
            )
            next_id += 1
    return conform_frame("inventory_snapshots", pl.DataFrame(rows)).sort("snapshot_id")


def _generate_shipments(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    commerce: FrameMap,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = random.numpy("operations.shipments")
    orders = commerce["orders"]
    order_items = commerce["order_items"]
    products = dimensions["products"].select("product_id", "warehouse_id")
    first_items = (
        order_items.sort("order_item_id")
        .group_by("order_id", maintain_order=True)
        .agg(pl.col("product_id").first())
        .join(products, on="product_id", how="left")
    )
    shipment_base = orders.join(first_items.select("order_id", "warehouse_id"), on="order_id")
    carrier_values = np.asarray([item.value for item in config.carriers], dtype=object)
    carrier_probabilities = probability_vector([item.weight for item in config.carriers])
    service_days = {item.code: item.delivery_days for item in config.regions}

    rows: list[dict[str, object]] = []
    order_status: dict[str, str] = {}
    for index, row in enumerate(shipment_base.iter_rows(named=True), start=1):
        ordered_at = row["ordered_at"]
        delivery_service = str(rng.choice(["standard", "express"], p=[0.78, 0.22]))
        handling_hours = float(rng.uniform(3.0, 28.0 if delivery_service == "standard" else 16.0))
        shipped_at = ordered_at + timedelta(hours=handling_hours)
        base_days = service_days[str(row["region"])]
        promised_days = base_days if delivery_service == "standard" else max(1, base_days - 1)
        promised_at = ordered_at + timedelta(days=promised_days, hours=20)
        is_late = bool(rng.random() < config.behavior.base_late_delivery_rate)
        transit_centre = promised_days - (0.35 if delivery_service == "standard" else 0.20)
        transit_days = max(0.6, float(rng.normal(transit_centre, 0.50)))
        if is_late:
            transit_days += float(rng.uniform(1.0, 3.0))
        delivered_candidate = shipped_at + timedelta(days=transit_days)
        delivered_at = delivered_candidate if delivered_candidate <= config.end_at else None
        status = "delivered" if delivered_at is not None else "in_transit"
        late = bool(delivered_at is not None and delivered_at > promised_at)
        order_status[str(row["order_id"])] = status
        rows.append(
            {
                "shipment_id": f"SHP-{index:010d}",
                "order_id": row["order_id"],
                "warehouse_id": row["warehouse_id"],
                "carrier": str(rng.choice(carrier_values, p=carrier_probabilities)),
                "delivery_service": delivery_service,
                "shipped_at": shipped_at,
                "promised_delivery_at": promised_at,
                "delivered_at": delivered_at,
                "status": status,
                "scan_count": 0,
                "late": late,
            }
        )

    shipments = conform_frame("shipments", pl.DataFrame(rows)).sort("shipment_id")
    status_updates = pl.DataFrame(
        {"order_id": list(order_status), "fulfilment_status": list(order_status.values())}
    )
    updated_orders = (
        orders.join(status_updates, on="order_id", how="left")
        .with_columns(pl.coalesce("fulfilment_status", "status").alias("status"))
        .drop("fulfilment_status")
    )
    return shipments, conform_frame("orders", updated_orders).sort("order_id")


def _generate_scan_events(
    random: RandomFactory,
    shipments: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Generate source and platform receipt timestamps for warehouse scan events."""

    rng = random.numpy("operations.warehouse_scan_events")
    rows: list[dict[str, object]] = []
    scan_counts: dict[str, int] = {}
    next_id = 1
    for shipment in shipments.iter_rows(named=True):
        shipped_at = shipment["shipped_at"]
        delivered_at = shipment["delivered_at"]
        packed_at = shipped_at - timedelta(hours=float(rng.uniform(1.5, 3.5)))
        departed_at = shipped_at - timedelta(minutes=float(rng.uniform(20.0, 75.0)))
        events: list[tuple[str, datetime]] = [
            ("packed", packed_at),
            ("departed_warehouse", departed_at),
            ("carrier_handoff", shipped_at),
        ]
        if delivered_at is not None:
            out_for_delivery = max(
                shipped_at + timedelta(minutes=1),
                delivered_at - timedelta(hours=float(rng.uniform(2.0, 8.0))),
            )
            events.extend(
                [
                    ("out_for_delivery", out_for_delivery),
                    ("delivered", delivered_at),
                ]
            )

        for sequence_number, (event_type, source_event_at) in enumerate(events, start=1):
            connector_batch_seconds = int(rng.choice([30, 60, 120], p=[0.58, 0.34, 0.08]))
            processing_seconds = int(np.clip(rng.lognormal(np.log(8.0), 0.45), 1, 45))
            ingestion_lag_seconds = connector_batch_seconds + processing_seconds
            rows.append(
                {
                    "scan_event_id": f"SCN-{next_id:012d}",
                    "shipment_id": shipment["shipment_id"],
                    "order_id": shipment["order_id"],
                    "warehouse_id": shipment["warehouse_id"],
                    "event_type": event_type,
                    "sequence_number": sequence_number,
                    "source_event_at": source_event_at,
                    "platform_received_at": source_event_at
                    + timedelta(seconds=ingestion_lag_seconds),
                    "ingestion_lag_seconds": ingestion_lag_seconds,
                    "connector_batch_seconds": connector_batch_seconds,
                }
            )
            next_id += 1
        scan_counts[str(shipment["shipment_id"])] = len(events)

    scan_events = conform_frame("warehouse_scan_events", pl.DataFrame(rows)).sort("scan_event_id")
    scan_count_frame = pl.DataFrame(
        {"shipment_id": list(scan_counts), "actual_scan_count": list(scan_counts.values())}
    )
    updated_shipments = (
        shipments.join(scan_count_frame, on="shipment_id", how="left")
        .with_columns(pl.col("actual_scan_count").alias("scan_count"))
        .drop("actual_scan_count")
    )
    return scan_events, conform_frame("shipments", updated_shipments).sort("shipment_id")


def _generate_returns_and_refunds(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    data: FrameMap,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = random.numpy("operations.returns")
    delivered = data["shipments"].filter(pl.col("status") == "delivered")
    if delivered.is_empty():
        return empty_frame("returns"), empty_frame("refunds")

    products = dimensions["products"].select("product_id", "return_propensity")
    customers = dimensions["customers"].select("customer_id", "baseline_return_propensity")
    candidate_items = (
        data["order_items"]
        .join(data["orders"].select("order_id", "customer_id"), on="order_id")
        .join(delivered.select("order_id", "delivered_at"), on="order_id")
        .join(products, on="product_id")
        .join(customers, on="customer_id")
    )

    return_rows: list[dict[str, object]] = []
    return_reasons = np.asarray(
        ["changed_mind", "not_as_described", "damaged", "wrong_size", "late_delivery"],
        dtype=object,
    )
    reason_probabilities = [0.34, 0.18, 0.12, 0.24, 0.12]
    returned_orders: set[str] = set()
    next_id = 1
    for row in candidate_items.iter_rows(named=True):
        order_id = str(row["order_id"])
        if order_id in returned_orders:
            continue
        probability = min(
            0.75,
            config.behavior.base_return_rate
            * (0.55 + float(row["return_propensity"]) * 2.2)
            * (0.60 + float(row["baseline_return_propensity"]) * 2.5),
        )
        if rng.random() >= probability:
            continue
        requested_at = row["delivered_at"] + timedelta(days=float(rng.uniform(1.0, 14.0)))
        if requested_at > config.end_at:
            continue
        received_candidate = requested_at + timedelta(days=float(rng.uniform(2.0, 8.0)))
        received_at = received_candidate if received_candidate <= config.end_at else None
        status = "received" if received_at is not None else "in_transit"
        return_rows.append(
            {
                "return_id": f"RET-{next_id:010d}",
                "order_id": order_id,
                "order_item_id": row["order_item_id"],
                "customer_id": row["customer_id"],
                "requested_at": requested_at,
                "received_at": received_at,
                "reason": str(rng.choice(return_reasons, p=reason_probabilities)),
                "status": status,
                "refund_amount": round(float(row["line_total"]), 2),
            }
        )
        returned_orders.add(order_id)
        next_id += 1

    if not return_rows:
        return empty_frame("returns"), empty_frame("refunds")
    returns = conform_frame("returns", pl.DataFrame(return_rows)).sort("return_id")

    refund_rows: list[dict[str, object]] = []
    for index, row in enumerate(returns.iter_rows(named=True), start=1):
        initiated_at = (row["received_at"] or row["requested_at"]) + timedelta(
            hours=float(rng.uniform(0.5, 12.0))
        )
        completes = bool(
            row["received_at"] is not None and rng.random() < config.behavior.refund_completion_rate
        )
        completed_candidate = initiated_at + timedelta(hours=float(rng.uniform(2.0, 72.0)))
        completed_at = (
            completed_candidate if completes and completed_candidate <= config.end_at else None
        )
        refund_rows.append(
            {
                "refund_id": f"RFD-{index:010d}",
                "return_id": row["return_id"],
                "order_id": row["order_id"],
                "initiated_at": initiated_at,
                "completed_at": completed_at,
                "status": "completed" if completed_at is not None else "pending",
                "amount": row["refund_amount"],
                "processor": str(rng.choice(["adyen-sim", "stripe-sim", "checkout-sim"])),
            }
        )
    refunds = conform_frame("refunds", pl.DataFrame(refund_rows)).sort("refund_id")
    return returns, refunds


def _generate_seller_feed_runs(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
) -> pl.DataFrame:
    rng = random.numpy("operations.seller_feeds")
    sellers = dimensions["sellers"]
    rows: list[dict[str, object]] = []
    next_id = 1
    for day_offset in range(config.duration_days):
        day_start = config.start_at + timedelta(days=day_offset)
        for seller in sellers.iter_rows(named=True):
            started_at = day_start + timedelta(hours=float(rng.uniform(0.0, 23.0)))
            latency = int(np.clip(rng.lognormal(np.log(75), 0.6), 5, 1_800))
            reliability = float(seller["feed_reliability"])
            success = bool(rng.random() < reliability)
            records = int(rng.integers(20, 2_501))
            rejected = int(rng.binomial(records, 0.002 if success else 0.08))
            rows.append(
                {
                    "feed_run_id": f"FED-{next_id:012d}",
                    "seller_id": seller["seller_id"],
                    "started_at": started_at,
                    "completed_at": started_at + timedelta(seconds=latency),
                    "schema_version": seller["feed_schema_version"],
                    "records_received": records,
                    "records_rejected": rejected,
                    "status": "succeeded" if success else "failed",
                    "latency_seconds": latency,
                }
            )
            next_id += 1
    return conform_frame("seller_feed_runs", pl.DataFrame(rows)).sort("feed_run_id")


def _generate_pipeline_runs(
    config: SimulationConfig,
    random: RandomFactory,
    commerce: FrameMap,
) -> pl.DataFrame:
    rng = random.numpy("operations.pipelines")
    base_volume = max(commerce["checkout_sessions"].height // config.duration_days, 1)
    rows: list[dict[str, object]] = []
    next_id = 1
    for day_offset in range(config.duration_days):
        day_start = config.start_at + timedelta(days=day_offset)
        for pipeline_position, pipeline in enumerate(config.pipelines):
            started_at = day_start + timedelta(hours=1 + pipeline_position * 3)
            input_rows = int(max(1, rng.normal(base_volume, base_volume * 0.08)))
            status = "succeeded" if rng.random() < 0.995 else "retried"
            data_quality_passed = bool(status == "succeeded" or rng.random() < 0.98)
            output_ratio = float(rng.uniform(0.96, 1.02))
            output_rows = int(max(0, round(input_rows * output_ratio)))
            latency_minutes = float(np.clip(rng.lognormal(np.log(8), 0.4), 1, 45))
            rows.append(
                {
                    "pipeline_run_id": f"PIP-{next_id:010d}",
                    "pipeline_name": pipeline,
                    "started_at": started_at,
                    "completed_at": started_at + timedelta(minutes=latency_minutes),
                    "input_rows": input_rows,
                    "output_rows": output_rows,
                    "status": status,
                    "data_quality_passed": data_quality_passed,
                    "code_version": f"2026.{1 + day_offset // 14}.{1 + pipeline_position}",
                }
            )
            next_id += 1
    return conform_frame("pipeline_runs", pl.DataFrame(rows)).sort("pipeline_run_id")


def _generate_deployments(
    config: SimulationConfig,
    random: RandomFactory,
) -> pl.DataFrame:
    rng = random.numpy("operations.deployments")
    region_values = np.asarray([item.code for item in config.regions], dtype=object)
    rows: list[dict[str, object]] = []
    next_id = 1
    interval_days = 7
    for service_position, service in enumerate(config.services):
        version_patch = 1
        for day_offset in range(service_position % 3, config.duration_days, interval_days):
            deployed_at = config.start_at + timedelta(
                days=day_offset,
                hours=int(rng.integers(8, 19)),
                minutes=int(rng.integers(0, 60)),
            )
            regional = bool(rng.random() < 0.35)
            rows.append(
                {
                    "deployment_id": f"DEP-{next_id:08d}",
                    "service": service,
                    "version": f"{1 + service_position}.4.{version_patch}",
                    "region_scope": str(rng.choice(region_values)) if regional else "all",
                    "deployed_at": deployed_at,
                    "status": "succeeded",
                    "change_type": str(
                        rng.choice(["application", "configuration", "dependency", "feature_flag"])
                    ),
                    "rollback_available": True,
                }
            )
            version_patch += 1
            next_id += 1
    return conform_frame("deployments", pl.DataFrame(rows)).sort("deployment_id")

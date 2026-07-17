"""Generate checkout, payment, order, and order-item events."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import numpy as np
import numpy.typing as npt
import polars as pl

from paic.simulator.config import SimulationConfig
from paic.simulator.randomness import RandomFactory
from paic.simulator.schema import conform_frame
from paic.simulator.types import FrameMap
from paic.simulator.utils import (
    clipped_probabilities,
    probability_vector,
    rounded_money,
    stable_ids,
)

_HOURLY_TRAFFIC: npt.NDArray[np.float64] = np.asarray(
    [
        0.25,
        0.18,
        0.14,
        0.12,
        0.15,
        0.25,
        0.45,
        0.72,
        0.90,
        1.02,
        1.08,
        1.12,
        1.08,
        1.04,
        1.06,
        1.12,
        1.24,
        1.42,
        1.58,
        1.66,
        1.52,
        1.18,
        0.80,
        0.48,
    ],
    dtype=np.float64,
)
_DAY_OF_WEEK_TRAFFIC = np.asarray([0.92, 0.95, 0.98, 1.02, 1.10, 1.20, 1.12])
_APP_VERSIONS = np.asarray(["7.3.0", "7.3.1", "7.4.0", "7.4.1"], dtype=object)
_PROCESSORS = np.asarray(["adyen-sim", "stripe-sim", "checkout-sim"], dtype=object)


def generate_commerce_events(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
) -> FrameMap:
    """Generate normal checkout, payment, order, and item data."""

    session_state = _generate_session_state(config, random, dimensions)
    payments = _generate_payments(config, random, dimensions, session_state)
    orders, order_items, payments, session_state = _generate_orders(
        config,
        random,
        dimensions,
        session_state,
        payments,
    )
    sessions = _finalize_sessions(session_state)
    return {
        "checkout_sessions": sessions,
        "payment_attempts": payments,
        "orders": orders,
        "order_items": order_items,
    }


def _hourly_schedule(config: SimulationConfig, rng: np.random.Generator) -> list[datetime]:
    hours: list[datetime] = []
    for offset in range(config.duration_days * 24):
        hour = config.start_at + timedelta(hours=offset)
        day_index = offset // 24
        trend = 0.97 + 0.06 * (day_index / max(config.duration_days - 1, 1))
        weekend = _DAY_OF_WEEK_TRAFFIC[hour.weekday()]
        mean = config.scale.base_sessions_per_hour * _HOURLY_TRAFFIC[hour.hour] * weekend * trend
        count = int(rng.poisson(mean))
        hours.extend([hour] * count)
    if not hours:
        hours.append(config.start_at)
    return hours


def _generate_session_state(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
) -> dict[str, object]:
    rng = random.numpy("events.checkout_sessions")
    hour_starts = _hourly_schedule(config, rng)
    count = len(hour_starts)
    session_ids = stable_ids("SES", count, width=10)

    customers = dimensions["customers"]
    products = dimensions["products"]
    promotions = dimensions["promotions"]

    customer_ids = customers.get_column("customer_id").to_numpy()
    customer_regions = customers.get_column("home_region").to_numpy()
    preferred_devices = customers.get_column("preferred_device").to_numpy()
    preferred_methods = customers.get_column("preferred_payment_method").to_numpy()
    issuers = customers.get_column("issuer").to_numpy()
    activity_weights = probability_vector(
        customers.get_column("baseline_activity_score").to_numpy()
    )

    product_ids = products.get_column("product_id").to_numpy()
    product_prices = products.get_column("base_price").to_numpy()
    product_categories = products.get_column("category").to_numpy()
    product_demand = probability_vector(products.get_column("demand_weight").to_numpy())

    customer_indices = rng.choice(len(customer_ids), size=count, p=activity_weights)
    product_indices = rng.choice(len(product_ids), size=count, p=product_demand)
    is_returning = rng.random(count) < config.behavior.returning_customer_share

    region_values = np.asarray([item.code for item in config.regions], dtype=object)
    region_probabilities = probability_vector([item.weight for item in config.regions])
    regions = rng.choice(region_values, size=count, p=region_probabilities).astype(object)
    regions[is_returning] = customer_regions[customer_indices[is_returning]]

    device_values = np.asarray([item.value for item in config.devices], dtype=object)
    device_probabilities = probability_vector([item.weight for item in config.devices])
    devices = rng.choice(device_values, size=count, p=device_probabilities).astype(object)
    use_preferred_device = rng.random(count) < config.behavior.preferred_device_adherence
    devices[use_preferred_device] = preferred_devices[customer_indices[use_preferred_device]]

    channel_values = np.asarray([item.value for item in config.channels], dtype=object)
    channel_probabilities = probability_vector([item.weight for item in config.channels])
    channels = rng.choice(channel_values, size=count, p=channel_probabilities).astype(object)
    campaign_ids: list[str | None] = []
    for channel in channels:
        channel_name = str(channel)
        if channel_name in {"organic", "direct"}:
            campaign_ids.append(None)
        else:
            campaign_code = channel_name.replace("_", "-").upper()
            campaign_ids.append(f"CAM-{campaign_code}-{int(rng.integers(1, 5)):02d}")

    starts = [
        hour + timedelta(seconds=int(second))
        for hour, second in zip(hour_starts, rng.integers(0, 3_600, size=count), strict=True)
    ]
    app_version_probabilities = np.asarray([0.08, 0.18, 0.46, 0.28])
    app_versions = rng.choice(_APP_VERSIONS, size=count, p=app_version_probabilities).astype(object)
    app_versions[np.asarray(devices) == "web"] = "web-2026.07"

    selected_promotion_ids, selected_discounts = _select_promotions(
        config,
        rng,
        promotions,
        starts,
        regions,
        channels,
        product_categories[product_indices],
    )
    primary_prices = product_prices[product_indices].astype(np.float64)
    basket_factor = 1.0 + rng.gamma(
        shape=1.2,
        scale=max(config.behavior.average_items_per_order - 1.0, 0.01),
        size=count,
    )
    basket_factor = np.clip(basket_factor, 1.0, 4.5)
    expected_amount = rounded_money(primary_prices * basket_factor * (1.0 - selected_discounts))

    region_conversion = {item.code: item.conversion_multiplier for item in config.regions}
    conversion_multipliers: npt.NDArray[np.float64] = np.asarray(
        [region_conversion[str(region)] for region in regions], dtype=np.float64
    )
    address_probability = clipped_probabilities(
        config.behavior.address_success_rate * conversion_multipliers
    )
    inventory_probability = clipped_probabilities(
        config.behavior.inventory_success_rate * np.sqrt(conversion_multipliers)
    )
    address_reached = rng.random(count) < config.behavior.address_attempt_rate
    address_ok = address_reached & (rng.random(count) < address_probability)
    inventory_reached = address_ok
    inventory_ok = inventory_reached & (rng.random(count) < inventory_probability)
    payment_reached = inventory_ok & (rng.random(count) < config.behavior.payment_attempt_rate)

    address_times: list[datetime | None] = []
    inventory_times: list[datetime | None] = []
    payment_times: list[datetime | None] = []
    for start, reached_address, reached_inventory, has_payment in zip(
        starts, address_reached, inventory_reached, payment_reached, strict=True
    ):
        address_at = (
            start + timedelta(seconds=int(rng.integers(10, 121))) if reached_address else None
        )
        inventory_at = (
            address_at + timedelta(seconds=int(rng.integers(1, 16)))
            if address_at is not None and reached_inventory
            else None
        )
        payment_at = (
            inventory_at + timedelta(seconds=int(rng.integers(4, 91)))
            if inventory_at is not None and has_payment
            else None
        )
        address_times.append(address_at)
        inventory_times.append(inventory_at)
        payment_times.append(payment_at)

    return {
        "session_id": session_ids,
        "customer_index": customer_indices,
        "customer_id": customer_ids[customer_indices].tolist(),
        "customer_preferred_payment": preferred_methods[customer_indices],
        "customer_issuer": issuers[customer_indices],
        "product_index": product_indices,
        "product_id": product_ids[product_indices].tolist(),
        "promotion_id": selected_promotion_ids,
        "promotion_discount": selected_discounts,
        "campaign_id": campaign_ids,
        "region": regions,
        "device": devices,
        "channel": channels,
        "customer_type": np.where(is_returning, "returning", "new").astype(object),
        "app_version": app_versions,
        "started_at": starts,
        "address_submitted_at": address_times,
        "inventory_checked_at": inventory_times,
        "payment_started_at": payment_times,
        "completed_at": [None] * count,
        "address_reached": address_reached,
        "address_ok": address_ok,
        "inventory_ok": inventory_ok,
        "payment_reached": payment_reached,
        "approved": np.zeros(count, dtype=np.bool_),
        "expected_amount": expected_amount,
    }


def _select_promotions(
    config: SimulationConfig,
    rng: np.random.Generator,
    promotions: pl.DataFrame,
    starts: list[datetime],
    regions: np.ndarray[tuple[int], np.dtype[np.object_]],
    channels: np.ndarray[tuple[int], np.dtype[np.object_]],
    categories: np.ndarray[tuple[int], np.dtype[np.object_]],
) -> tuple[list[str | None], np.ndarray[tuple[int], np.dtype[np.float64]]]:
    count = len(starts)
    result_ids: list[str | None] = [None] * count
    discounts: np.ndarray[tuple[int], np.dtype[np.float64]] = np.zeros(count, dtype=np.float64)
    if promotions.is_empty() or config.behavior.promotion_session_share <= 0:
        return result_ids, discounts

    records = promotions.to_dicts()
    selected_mask = rng.random(count) < config.behavior.promotion_session_share
    for index in np.flatnonzero(selected_mask):
        candidates = [
            item
            for item in records
            if item["start_at"] <= starts[index] < item["end_at"]
            and item["region"] == regions[index]
            and item["channel"] == channels[index]
            and item["category"] == categories[index]
        ]
        if candidates:
            selected = candidates[int(rng.integers(0, len(candidates)))]
            result_ids[int(index)] = str(selected["promotion_id"])
            discounts[int(index)] = float(selected["discount_pct"])
    return result_ids, discounts


def _generate_payments(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    state: dict[str, object],
) -> pl.DataFrame:
    rng = random.numpy("events.payment_attempts")
    reached = np.asarray(state["payment_reached"], dtype=np.bool_)
    indices = np.flatnonzero(reached)
    count = len(indices)
    if count == 0:
        raise RuntimeError("healthy baseline unexpectedly generated zero payment attempts")

    preferred_methods = np.asarray(state["customer_preferred_payment"], dtype=object)[indices]
    payment_values = np.asarray([item.value for item in config.payment_methods], dtype=object)
    payment_probabilities = probability_vector([item.weight for item in config.payment_methods])
    methods = rng.choice(payment_values, size=count, p=payment_probabilities).astype(object)
    adherence = rng.random(count) < config.behavior.preferred_payment_adherence
    methods[adherence] = preferred_methods[adherence]

    issuers = np.asarray(state["customer_issuer"], dtype=object)[indices]
    regions = np.asarray(state["region"], dtype=object)[indices]
    region_approval = {item.code: item.payment_approval_multiplier for item in config.regions}
    method_multiplier = {
        "card": 1.0,
        "wallet": 1.01,
        "bank_transfer": 0.97,
        "buy_now_pay_later": 0.94,
        "cash_on_delivery": 0.985,
    }
    issuer_multiplier = {
        "BANK-A": 1.01,
        "BANK-B": 0.995,
        "BANK-C": 0.98,
        "BANK-D": 1.005,
        "WALLET-NETWORK": 1.01,
    }
    fraud_scores = np.clip(rng.beta(1.6, 12.0, size=count), 0.001, 0.98)
    approval_probabilities: npt.NDArray[np.float64] = np.asarray(
        [
            config.behavior.base_payment_approval_rate
            * region_approval[str(region)]
            * method_multiplier.get(str(method), 0.98)
            * issuer_multiplier.get(str(issuer), 0.985)
            * (1.0 - 0.30 * float(fraud_score))
            for region, method, issuer, fraud_score in zip(
                regions, methods, issuers, fraud_scores, strict=True
            )
        ],
        dtype=np.float64,
    )
    approval_probabilities = clipped_probabilities(approval_probabilities)
    approved = rng.random(count) < approval_probabilities
    state_approved = np.asarray(state["approved"], dtype=np.bool_)
    state_approved[indices] = approved
    state["approved"] = state_approved

    latency = np.clip(rng.lognormal(np.log(420), 0.45, size=count), 80, 5_000).astype(np.int64)
    decline_reasons: list[str | None] = []
    for is_approved, score in zip(approved, fraud_scores, strict=True):
        if is_approved:
            decline_reasons.append(None)
        elif score > 0.42:
            decline_reasons.append("risk_rejected")
        else:
            decline_reasons.append(
                str(rng.choice(["issuer_declined", "insufficient_funds", "authentication_failed"]))
            )

    currencies = {item.code: item.currency for item in config.regions}
    attempts = pl.DataFrame(
        {
            "payment_attempt_id": stable_ids("PAY", count, width=10),
            "session_id": np.asarray(state["session_id"], dtype=object)[indices].tolist(),
            "customer_id": np.asarray(state["customer_id"], dtype=object)[indices].tolist(),
            "payment_method": methods.tolist(),
            "issuer": issuers.tolist(),
            "processor": rng.choice(_PROCESSORS, size=count, p=[0.42, 0.38, 0.20]).tolist(),
            "attempted_at": np.asarray(state["payment_started_at"], dtype=object)[indices].tolist(),
            "amount": np.asarray(state["expected_amount"], dtype=np.float64)[indices],
            "currency": [currencies[str(region)] for region in regions],
            "approved": approved,
            "decline_reason": decline_reasons,
            "latency_ms": latency,
            "fraud_score": fraud_scores,
            "fraud_rule_version": ["17"] * count,
        }
    )
    return conform_frame("payment_attempts", attempts).sort("payment_attempt_id")


def _generate_orders(
    config: SimulationConfig,
    random: RandomFactory,
    dimensions: FrameMap,
    state: dict[str, object],
    payments: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, object]]:
    rng = random.numpy("events.orders")
    approved_payments = payments.filter(pl.col("approved"))
    count = approved_payments.height
    if count == 0:
        raise RuntimeError("healthy baseline unexpectedly generated zero approved payments")

    session_ids = np.asarray(state["session_id"], dtype=object)
    session_to_index = {str(value): index for index, value in enumerate(session_ids)}
    approved_session_ids = approved_payments.get_column("session_id").to_list()
    session_indices: npt.NDArray[np.int64] = np.asarray(
        [session_to_index[str(session_id)] for session_id in approved_session_ids], dtype=np.int64
    )

    products = dimensions["products"]
    product_ids = products.get_column("product_id").to_numpy()
    product_sellers = products.get_column("seller_id").to_numpy()
    product_prices = products.get_column("base_price").to_numpy().astype(np.float64)
    product_demand = probability_vector(products.get_column("demand_weight").to_numpy())
    product_index_lookup = {str(value): index for index, value in enumerate(product_ids)}

    order_ids = stable_ids("ORD", count, width=10)
    item_counts = 1 + rng.poisson(
        max(config.behavior.average_items_per_order - 1.0, 0.0), size=count
    )
    item_counts = np.clip(item_counts, 1, 4).astype(np.int64)

    item_rows: list[dict[str, object]] = []
    subtotals = np.zeros(count, dtype=np.float64)
    discounts: np.ndarray[tuple[int], np.dtype[np.float64]] = np.zeros(count, dtype=np.float64)
    next_item = 1
    session_product_ids = np.asarray(state["product_id"], dtype=object)
    session_promotion_discounts = np.asarray(state["promotion_discount"], dtype=np.float64)

    for order_position, (order_id, session_index, item_count) in enumerate(
        zip(order_ids, session_indices, item_counts, strict=True)
    ):
        primary_id = str(session_product_ids[session_index])
        primary_index = product_index_lookup[primary_id]
        selected_indices = [primary_index]
        if item_count > 1:
            selected_indices.extend(
                rng.choice(
                    len(product_ids),
                    size=int(item_count - 1),
                    replace=True,
                    p=product_demand,
                ).tolist()
            )
        promotion_discount = float(session_promotion_discounts[session_index])
        for product_index in selected_indices:
            quantity = int(rng.choice([1, 1, 1, 2], p=[0.55, 0.20, 0.15, 0.10]))
            unit_price = float(product_prices[int(product_index)])
            line_subtotal = unit_price * quantity
            line_discount = line_subtotal * promotion_discount
            line_total = line_subtotal - line_discount
            subtotals[order_position] += line_subtotal
            discounts[order_position] += line_discount
            item_rows.append(
                {
                    "order_item_id": f"ITM-{next_item:011d}",
                    "order_id": order_id,
                    "product_id": str(product_ids[int(product_index)]),
                    "seller_id": str(product_sellers[int(product_index)]),
                    "quantity": quantity,
                    "unit_price": round(unit_price, 2),
                    "discount_amount": round(line_discount, 2),
                    "line_total": round(line_total, 2),
                }
            )
            next_item += 1

    regions = np.asarray(state["region"], dtype=object)[session_indices]
    tax_rates = {item.code: item.tax_rate for item in config.regions}
    currencies = {item.code: item.currency for item in config.regions}
    taxable = subtotals - discounts
    taxes = rounded_money(
        [taxable[index] * tax_rates[str(region)] for index, region in enumerate(regions)]
    )
    shipping = rounded_money(np.where(taxable >= 75.0, 0.0, 5.99))
    totals = rounded_money(taxable + taxes + shipping)
    ordered_at = [
        value + timedelta(milliseconds=int(rng.integers(100, 1_501)))
        for value in approved_payments.get_column("attempted_at").to_list()
    ]

    orders = pl.DataFrame(
        {
            "order_id": order_ids,
            "session_id": approved_session_ids,
            "customer_id": approved_payments.get_column("customer_id").to_list(),
            "payment_attempt_id": approved_payments.get_column("payment_attempt_id").to_list(),
            "ordered_at": ordered_at,
            "region": regions.tolist(),
            "channel": np.asarray(state["channel"], dtype=object)[session_indices].tolist(),
            "campaign_id": np.asarray(state["campaign_id"], dtype=object)[session_indices].tolist(),
            "status": ["placed"] * count,
            "subtotal": rounded_money(subtotals),
            "discount_amount": rounded_money(discounts),
            "tax_amount": taxes,
            "shipping_amount": shipping,
            "total_amount": totals,
            "currency": [currencies[str(region)] for region in regions],
            "item_count": item_counts,
        }
    )
    order_items = pl.DataFrame(item_rows)

    payment_totals = pl.DataFrame(
        {
            "payment_attempt_id": approved_payments.get_column("payment_attempt_id"),
            "order_total": totals,
        }
    )
    payments = (
        payments.join(payment_totals, on="payment_attempt_id", how="left")
        .with_columns(pl.coalesce("order_total", "amount").alias("amount"))
        .drop("order_total")
    )

    completed_at = list(cast(list[datetime | None], state["completed_at"]))
    expected_amount = np.asarray(state["expected_amount"], dtype=np.float64)
    for session_index, ordered, total in zip(session_indices, ordered_at, totals, strict=True):
        completed_at[int(session_index)] = ordered
        expected_amount[int(session_index)] = total
    state["completed_at"] = completed_at
    state["expected_amount"] = expected_amount

    return (
        conform_frame("orders", orders).sort("order_id"),
        conform_frame("order_items", order_items).sort("order_item_id"),
        conform_frame("payment_attempts", payments).sort("payment_attempt_id"),
        state,
    )


def _finalize_sessions(state: dict[str, object]) -> pl.DataFrame:
    address_reached = np.asarray(state["address_reached"], dtype=np.bool_)
    address_ok = np.asarray(state["address_ok"], dtype=np.bool_)
    inventory_ok = np.asarray(state["inventory_ok"], dtype=np.bool_)
    payment_reached = np.asarray(state["payment_reached"], dtype=np.bool_)
    approved = np.asarray(state["approved"], dtype=np.bool_)

    stage: np.ndarray[tuple[int], np.dtype[np.object_]] = np.full(
        len(address_ok), "checkout_started", dtype=object
    )
    reason: np.ndarray[tuple[int], np.dtype[np.object_]] = np.full(
        len(address_ok), "customer_abandoned", dtype=object
    )
    stage[address_reached] = "address_submitted"
    reason[address_reached] = "address_validation_failed"
    stage[address_ok] = "inventory_checked"
    reason[address_ok] = "inventory_unavailable"
    stage[inventory_ok] = "inventory_confirmed"
    reason[inventory_ok] = "before_payment"
    stage[payment_reached] = "payment_started"
    reason[payment_reached] = "payment_declined"
    stage[approved] = "order_completed"
    reason[approved] = None

    frame = pl.DataFrame(
        {
            "session_id": state["session_id"],
            "customer_id": state["customer_id"],
            "product_id": state["product_id"],
            "promotion_id": state["promotion_id"],
            "campaign_id": state["campaign_id"],
            "region": np.asarray(state["region"], dtype=object).tolist(),
            "device": np.asarray(state["device"], dtype=object).tolist(),
            "channel": np.asarray(state["channel"], dtype=object).tolist(),
            "customer_type": np.asarray(state["customer_type"], dtype=object).tolist(),
            "app_version": np.asarray(state["app_version"], dtype=object).tolist(),
            "started_at": state["started_at"],
            "address_submitted_at": state["address_submitted_at"],
            "inventory_checked_at": state["inventory_checked_at"],
            "payment_started_at": state["payment_started_at"],
            "completed_at": state["completed_at"],
            "stage_reached": stage.tolist(),
            "abandonment_reason": reason.tolist(),
            "expected_amount": rounded_money(state["expected_amount"]),
        }
    )
    return conform_frame("checkout_sessions", frame).sort("session_id")

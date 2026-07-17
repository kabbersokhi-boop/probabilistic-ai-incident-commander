"""Generate deterministic commerce dimension tables."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import numpy.typing as npt
import polars as pl

from paic.simulator.config import SimulationConfig
from paic.simulator.randomness import RandomFactory
from paic.simulator.schema import conform_frame
from paic.simulator.types import FrameMap
from paic.simulator.utils import (
    probability_vector,
    random_datetimes,
    rounded_money,
    stable_ids,
    values_and_probabilities,
)


def generate_dimensions(config: SimulationConfig, random: RandomFactory) -> FrameMap:
    """Generate customers, sellers, warehouses, products, and promotions."""

    warehouses = _generate_warehouses(config, random)
    sellers = _generate_sellers(config, random)
    customers = _generate_customers(config, random)
    products = _generate_products(config, random, sellers, warehouses)
    promotions = _generate_promotions(config, random)
    return {
        "customers": customers,
        "sellers": sellers,
        "warehouses": warehouses,
        "products": products,
        "promotions": promotions,
    }


def _generate_customers(config: SimulationConfig, random: RandomFactory) -> pl.DataFrame:
    rng = random.numpy("dimensions.customers")
    count = config.scale.customers
    ids = stable_ids("CUS", count)
    region_values = np.asarray([item.code for item in config.regions], dtype=object)
    region_probabilities = probability_vector([item.weight for item in config.regions])
    device_values, device_probabilities = values_and_probabilities(config.devices)
    payment_values, payment_probabilities = values_and_probabilities(config.payment_methods)
    issuer_values, issuer_probabilities = values_and_probabilities(config.issuers)
    acquisition_values, acquisition_probabilities = values_and_probabilities(
        config.acquisition_channels
    )

    activity = np.clip(rng.lognormal(mean=0.0, sigma=0.8, size=count), 0.08, 12.0)
    price_sensitivity = rng.beta(2.4, 2.2, size=count)
    return_propensity = np.clip(rng.beta(1.7, 11.0, size=count), 0.01, 0.65)
    loyalty_score = activity * (1.2 - price_sensitivity)
    loyalty_tier = np.select(
        [loyalty_score >= 2.6, loyalty_score >= 1.25, loyalty_score >= 0.55],
        ["platinum", "gold", "silver"],
        default="standard",
    )
    customer_segment = np.select(
        [
            (activity >= 2.0) & (price_sensitivity < 0.45),
            price_sensitivity >= 0.7,
            return_propensity >= 0.25,
        ],
        ["high_value", "deal_seeker", "return_prone"],
        default="mainstream",
    )

    signup_start = config.start_at - timedelta(days=1_825)
    signup_end = config.start_at - timedelta(hours=1)
    created_at = random_datetimes(rng, signup_start, signup_end, count)

    frame = pl.DataFrame(
        {
            "customer_id": ids,
            "created_at": created_at,
            "home_region": rng.choice(region_values, size=count, p=region_probabilities).tolist(),
            "preferred_device": rng.choice(
                device_values, size=count, p=device_probabilities
            ).tolist(),
            "preferred_payment_method": rng.choice(
                payment_values, size=count, p=payment_probabilities
            ).tolist(),
            "issuer": rng.choice(issuer_values, size=count, p=issuer_probabilities).tolist(),
            "loyalty_tier": loyalty_tier.tolist(),
            "acquisition_channel": rng.choice(
                acquisition_values, size=count, p=acquisition_probabilities
            ).tolist(),
            "customer_segment": customer_segment.tolist(),
            "baseline_activity_score": activity,
            "baseline_price_sensitivity": price_sensitivity,
            "baseline_return_propensity": return_propensity,
        }
    )
    return conform_frame("customers", frame).sort("customer_id")


def _generate_sellers(config: SimulationConfig, random: RandomFactory) -> pl.DataFrame:
    rng = random.numpy("dimensions.sellers")
    fake = random.faker("dimensions.sellers.names")
    count = config.scale.sellers
    reserved_ids = ["S-101", "S-204", "S-305"]
    generic_ids = [f"S-{1_000 + index}" for index in range(max(count - len(reserved_ids), 0))]
    ids = (reserved_ids + generic_ids)[:count]
    region_values = np.asarray([item.code for item in config.regions], dtype=object)
    region_probabilities = probability_vector([item.weight for item in config.regions])
    category_values, category_probabilities = values_and_probabilities(config.categories)

    reliability = np.clip(rng.beta(25.0, 1.8, size=count), 0.8, 0.999)
    rating = np.clip(3.0 + reliability * 1.8 + rng.normal(0, 0.15, size=count), 2.8, 5.0)
    seller_tier = np.select(
        [rating >= 4.65, rating >= 4.25, rating >= 3.8],
        ["strategic", "preferred", "verified"],
        default="standard",
    )
    joined_at = random_datetimes(
        rng,
        config.start_at - timedelta(days=2_500),
        config.start_at - timedelta(days=30),
        count,
    )
    home_regions = rng.choice(region_values, size=count, p=region_probabilities).astype(object)
    category_focus = rng.choice(category_values, size=count, p=category_probabilities).astype(
        object
    )
    feed_formats = rng.choice(
        np.asarray(["json", "csv", "xml"], dtype=object),
        size=count,
        p=[0.55, 0.35, 0.10],
    ).astype(object)
    feed_versions = rng.choice(
        np.asarray(["v2", "v3"], dtype=object), size=count, p=[0.82, 0.18]
    ).astype(object)

    reserved_profiles = {
        "S-101": ("IN-NORTH", "home", "json", "v2"),
        "S-204": ("IN-SOUTH", "consumer-electronics", "json", "v2"),
        "S-305": ("IN-WEST", "apparel", "csv", "v2"),
    }
    valid_regions = set(region_values.tolist())
    valid_categories = set(category_values.tolist())
    for index, seller_id in enumerate(ids):
        profile = reserved_profiles.get(seller_id)
        if profile is None:
            continue
        region, category, feed_format, feed_version = profile
        if region in valid_regions:
            home_regions[index] = region
        if category in valid_categories:
            category_focus[index] = category
        feed_formats[index] = feed_format
        feed_versions[index] = feed_version

    frame = pl.DataFrame(
        {
            "seller_id": ids,
            "seller_name": [fake.unique.company() for _ in range(count)],
            "home_region": home_regions.tolist(),
            "category_focus": category_focus.tolist(),
            "seller_tier": seller_tier.tolist(),
            "rating": np.round(rating, 2),
            "feed_format": feed_formats.tolist(),
            "feed_schema_version": feed_versions.tolist(),
            "feed_reliability": reliability,
            "joined_at": joined_at,
        }
    )
    return conform_frame("sellers", frame).sort("seller_id")


def _generate_warehouses(config: SimulationConfig, random: RandomFactory) -> pl.DataFrame:
    rng = random.numpy("dimensions.warehouses")
    count = config.scale.warehouses
    reserved = [
        ("W-12", "IN-NORTH"),
        ("W-17", "IN-WEST"),
        ("W-22", "IN-SOUTH"),
        ("W-31", "IN-EAST"),
    ]
    generic_ids = [f"W-{100 + index}" for index in range(max(count - len(reserved), 0))]
    ids = [item[0] for item in reserved[:count]] + generic_ids
    region_codes = [item.code for item in config.regions]
    reserved_regions = [
        region if region in region_codes else region_codes[index % len(region_codes)]
        for index, (_, region) in enumerate(reserved[:count])
    ]
    assigned_regions = reserved_regions + [
        region_codes[index % len(region_codes)] for index in range(len(generic_ids))
    ]
    region_lookup = {item.code: item for item in config.regions}
    service_days = [region_lookup[region].delivery_days for region in assigned_regions]
    active_from = random_datetimes(
        rng,
        config.start_at - timedelta(days=3_000),
        config.start_at - timedelta(days=365),
        count,
    )

    frame = pl.DataFrame(
        {
            "warehouse_id": ids,
            "region": assigned_regions,
            "capacity_units": rng.integers(50_000, 500_001, size=count),
            "service_level_days": service_days,
            "scan_reliability": np.clip(rng.beta(40.0, 1.4, size=count), 0.9, 0.9995),
            "active_from": active_from,
        }
    )
    return conform_frame("warehouses", frame).sort("warehouse_id")


def _generate_products(
    config: SimulationConfig,
    random: RandomFactory,
    sellers: pl.DataFrame,
    warehouses: pl.DataFrame,
) -> pl.DataFrame:
    rng = random.numpy("dimensions.products")
    count = config.scale.products
    ids = stable_ids("PRD", count, width=7)
    seller_ids = sellers.get_column("seller_id").to_numpy()
    seller_categories = sellers.get_column("category_focus").to_numpy()
    warehouse_ids = warehouses.get_column("warehouse_id").to_numpy()
    categories, category_probabilities = values_and_probabilities(config.categories)

    seller_indices = rng.integers(0, len(seller_ids), size=count)
    seller_indices[: len(seller_ids)] = np.arange(len(seller_ids))
    rng.shuffle(seller_indices)
    follow_focus = rng.random(count) < 0.72
    special_sellers = np.isin(
        seller_ids[seller_indices], np.asarray(["S-101", "S-204", "S-305"], dtype=object)
    )
    follow_focus[special_sellers] = True
    categories_selected = rng.choice(categories, size=count, p=category_probabilities).astype(
        object
    )
    categories_selected[follow_focus] = seller_categories[seller_indices[follow_focus]]

    category_price_centres = {
        "consumer-electronics": 120.0,
        "home": 55.0,
        "apparel": 45.0,
        "beauty": 28.0,
        "grocery": 18.0,
        "sports": 65.0,
        "books": 20.0,
        "toys": 35.0,
    }
    price_centres: npt.NDArray[np.float64] = np.asarray(
        [category_price_centres.get(str(category), 40.0) for category in categories_selected],
        dtype=np.float64,
    )
    prices = np.clip(
        rng.lognormal(np.log(price_centres), 0.55),
        3.0,
        2_500.0,
    )
    costs = prices * rng.uniform(0.42, 0.78, size=count)
    demand = np.clip(rng.lognormal(0.0, 1.1, size=count), 0.02, 50.0)
    return_propensity = np.clip(rng.beta(2.0, 14.0, size=count), 0.01, 0.65)
    active_from = random_datetimes(
        rng,
        config.start_at - timedelta(days=730),
        config.start_at - timedelta(days=2),
        count,
    )

    warehouse_indices = rng.integers(0, len(warehouse_ids), size=count)
    warehouse_indices[: len(warehouse_ids)] = np.arange(len(warehouse_ids))
    rng.shuffle(warehouse_indices)

    frame = pl.DataFrame(
        {
            "product_id": ids,
            "seller_id": seller_ids[seller_indices].tolist(),
            "warehouse_id": warehouse_ids[warehouse_indices].tolist(),
            "category": categories_selected.tolist(),
            "base_price": rounded_money(prices),
            "unit_cost": rounded_money(costs),
            "demand_weight": demand,
            "return_propensity": return_propensity,
            "reorder_point": rng.integers(5, 101, size=count),
            "active_from": active_from,
        }
    )
    return conform_frame("products", frame).sort("product_id")


def _generate_promotions(config: SimulationConfig, random: RandomFactory) -> pl.DataFrame:
    count = config.scale.promotions
    if count == 0:
        return conform_frame(
            "promotions",
            pl.DataFrame(
                {
                    "promotion_id": [],
                    "promotion_name": [],
                    "channel": [],
                    "region": [],
                    "category": [],
                    "start_at": [],
                    "end_at": [],
                    "discount_pct": [],
                    "budget": [],
                }
            ),
        )

    rng = random.numpy("dimensions.promotions")
    ids = stable_ids("PRO", count, width=5)
    channel_values, channel_probabilities = values_and_probabilities(config.channels)
    category_values, category_probabilities = values_and_probabilities(config.categories)
    region_values = np.asarray([item.code for item in config.regions], dtype=object)
    region_probabilities = probability_vector([item.weight for item in config.regions])
    starts = random_datetimes(
        rng,
        config.start_at - timedelta(days=3),
        config.end_at - timedelta(hours=12),
        count,
    )
    durations = rng.integers(12, max(13, min(config.duration_days * 24, 168)), size=count)
    ends = [
        min(start + timedelta(hours=int(duration)), config.end_at)
        for start, duration in zip(starts, durations, strict=True)
    ]
    labels = [
        f"{category.title()} {rng.choice(['Boost', 'Spotlight', 'Savings', 'Event'])} {index:02d}"
        for index, category in enumerate(
            rng.choice(category_values, size=count, p=category_probabilities), start=1
        )
    ]

    frame = pl.DataFrame(
        {
            "promotion_id": ids,
            "promotion_name": labels,
            "channel": rng.choice(channel_values, size=count, p=channel_probabilities).tolist(),
            "region": rng.choice(region_values, size=count, p=region_probabilities).tolist(),
            "category": rng.choice(category_values, size=count, p=category_probabilities).tolist(),
            "start_at": starts,
            "end_at": ends,
            "discount_pct": np.round(rng.choice([0.05, 0.10, 0.15, 0.20, 0.25], size=count), 2),
            "budget": rounded_money(rng.lognormal(10.0, 0.7, size=count)),
        }
    )
    return conform_frame("promotions", frame).sort("promotion_id")

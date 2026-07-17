"""Checkout funnel analysis with explicit denominators and drop-off rates."""

from __future__ import annotations

import polars as pl

from paic.analytics.config import AnalyticsConfig, CohortSpec
from paic.analytics.metrics import _GRAIN_DURATION, period_expression
from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.analytics.schema import conform_analytics_frame, empty_analytics_frame
from paic.analytics.types import FactMap

FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    ("checkout_started", "_checkout_started"),
    ("address_submitted", "_address_submitted"),
    ("inventory_checked", "_inventory_checked"),
    ("payment_started", "_payment_started"),
    ("order_completed", "_order_completed"),
)

_FUNNEL_DIMENSIONS = {
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
}


def _dimension_expressions(cohort: CohortSpec) -> list[pl.Expr]:
    enabled = set(cohort.dimensions)
    return [
        pl.col(name).cast(pl.String).alias(name)
        if name in enabled
        else pl.lit(None, dtype=pl.String).alias(name)
        for name in ANALYTIC_DIMENSIONS
    ]


def calculate_funnel_observations(facts: FactMap, config: AnalyticsConfig) -> pl.DataFrame:
    """Calculate stage counts and stepwise conversion for configured cohorts."""

    checkout = facts["checkout"]
    if checkout.is_empty():
        return empty_analytics_frame("funnel_observations")
    cohort_map = config.cohort_map
    selected = [cohort_map[name] for name in config.funnel_cohorts]
    unsupported = [
        item.name for item in selected if not set(item.dimensions).issubset(_FUNNEL_DIMENSIONS)
    ]
    if unsupported:
        raise ValueError(f"unsupported funnel cohorts: {', '.join(unsupported)}")

    outputs: list[pl.DataFrame] = []
    for grain in config.time_grains:
        periodized = checkout.with_columns(period_expression("started_at", grain))
        for cohort in selected:
            group_columns = ["period_start", *cohort.dimensions]
            grouped = periodized.group_by(group_columns, maintain_order=True).agg(
                pl.len().cast(pl.Int64).alias("_checkout_started"),
                pl.col("address_submitted").cast(pl.Int64).sum().alias("_address_submitted"),
                pl.col("inventory_checked").cast(pl.Int64).sum().alias("_inventory_checked"),
                pl.col("payment_started").cast(pl.Int64).sum().alias("_payment_started"),
                pl.col("has_order").cast(pl.Int64).sum().alias("_order_completed"),
            )
            for stage_order, (stage_name, stage_column) in enumerate(FUNNEL_STAGES, start=1):
                previous_column = (
                    FUNNEL_STAGES[stage_order - 2][1] if stage_order > 1 else stage_column
                )
                current = pl.col(stage_column)
                previous = pl.col(previous_column)
                started = pl.col("_checkout_started")
                quality = (
                    pl.when(current > previous)
                    .then(pl.lit("invalid"))
                    .when(previous < config.minimum_denominator)
                    .then(pl.lit("insufficient_data"))
                    .otherwise(pl.lit("ok"))
                )
                stage = grouped.with_columns(
                    pl.lit(grain).alias("time_grain"),
                    (pl.col("period_start") + _GRAIN_DURATION[grain]).alias("period_end"),
                    pl.lit(cohort.name).alias("cohort_name"),
                    pl.lit(len(cohort.dimensions), dtype=pl.Int64).alias("dimension_count"),
                    *_dimension_expressions(cohort),
                    pl.lit(stage_order, dtype=pl.Int64).alias("stage_order"),
                    pl.lit(stage_name).alias("stage_name"),
                    current.cast(pl.Int64).alias("stage_count"),
                    previous.cast(pl.Int64).alias("previous_stage_count"),
                    (
                        pl.when(previous > 0)
                        .then(current / previous)
                        .otherwise(None)
                        .cast(pl.Float64)
                        .alias("conversion_from_previous")
                    ),
                    (
                        pl.when(started > 0)
                        .then(current / started)
                        .otherwise(None)
                        .cast(pl.Float64)
                        .alias("conversion_from_start")
                    ),
                    (previous - current).cast(pl.Int64).alias("drop_off_count"),
                    (
                        pl.when(previous > 0)
                        .then((previous - current) / previous)
                        .otherwise(None)
                        .cast(pl.Float64)
                        .alias("drop_off_rate")
                    ),
                    quality.alias("quality_status"),
                )
                outputs.append(conform_analytics_frame("funnel_observations", stage))
    if not outputs:
        return empty_analytics_frame("funnel_observations")
    return pl.concat(outputs, how="vertical").sort(
        ["time_grain", "period_start", "cohort_name", *ANALYTIC_DIMENSIONS, "stage_order"],
        nulls_last=True,
    )

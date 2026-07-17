"""Deterministic metric aggregation over analytical facts."""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from paic.analytics.config import AnalyticsConfig, CohortSpec, TimeGrain
from paic.analytics.registry import ANALYTIC_DIMENSIONS, METRIC_REGISTRY, MetricDefinition
from paic.analytics.schema import conform_analytics_frame, empty_analytics_frame
from paic.analytics.types import FactMap

_GRAIN_INTERVAL = {"hour": "1h", "day": "1d"}
_GRAIN_DURATION = {
    "hour": pl.duration(hours=1),
    "day": pl.duration(days=1),
}


def period_expression(timestamp_column: str, grain: TimeGrain) -> pl.Expr:
    return pl.col(timestamp_column).dt.truncate(_GRAIN_INTERVAL[grain]).alias("period_start")


def _supported_cohorts(
    definition: MetricDefinition, cohorts: Iterable[CohortSpec]
) -> list[CohortSpec]:
    supported = set(definition.supported_dimensions)
    return [item for item in cohorts if set(item.dimensions).issubset(supported)]


def _aggregate_expressions(definition: MetricDefinition) -> list[pl.Expr]:
    row_count = pl.len().cast(pl.Int64).alias("_row_count")
    calculation = definition.calculation
    if calculation == "count":
        return [row_count]
    if calculation == "distinct_count":
        if definition.value_column is None:
            raise ValueError(f"metric {definition.name} requires value_column")
        return [
            row_count,
            pl.col(definition.value_column).n_unique().cast(pl.Int64).alias("_distinct_count"),
        ]
    if calculation in {"sum", "mean", "quantile"}:
        if definition.value_column is None:
            raise ValueError(f"metric {definition.name} requires value_column")
        expressions: list[pl.Expr] = [
            row_count,
            pl.col(definition.value_column).count().cast(pl.Int64).alias("_value_count"),
        ]
        if calculation in {"sum", "mean"}:
            expressions.append(
                pl.col(definition.value_column).sum().cast(pl.Float64).alias("_value_sum")
            )
        else:
            if definition.quantile is None:
                raise ValueError(f"metric {definition.name} requires quantile")
            expressions.append(
                pl.col(definition.value_column)
                .quantile(definition.quantile, interpolation="linear")
                .cast(pl.Float64)
                .alias("_quantile")
            )
        return expressions
    if calculation == "ratio":
        if definition.numerator_column is None:
            raise ValueError(f"metric {definition.name} requires numerator_column")
        denominator = (
            pl.col(definition.denominator_column).cast(pl.Int64).sum()
            if definition.denominator_column is not None
            else pl.len()
        )
        return [
            row_count,
            pl.col(definition.numerator_column)
            .cast(pl.Int64)
            .sum()
            .cast(pl.Float64)
            .alias("_numerator"),
            denominator.cast(pl.Float64).alias("_denominator"),
        ]
    if calculation == "ratio_of_sums":
        if definition.numerator_column is None or definition.denominator_column is None:
            raise ValueError(f"metric {definition.name} requires numerator and denominator columns")
        return [
            row_count,
            pl.col(definition.numerator_column).sum().cast(pl.Float64).alias("_numerator"),
            pl.col(definition.denominator_column).sum().cast(pl.Float64).alias("_denominator"),
        ]
    raise AssertionError(f"unknown calculation: {calculation}")


def _measurement_columns(definition: MetricDefinition) -> list[pl.Expr]:
    calculation = definition.calculation
    null_float = pl.lit(None, dtype=pl.Float64)
    if calculation == "count":
        return [
            pl.col("_row_count").cast(pl.Float64).alias("value"),
            pl.col("_row_count").cast(pl.Float64).alias("numerator"),
            null_float.alias("denominator"),
            pl.col("_row_count").cast(pl.Int64).alias("sample_size"),
        ]
    if calculation == "distinct_count":
        return [
            pl.col("_distinct_count").cast(pl.Float64).alias("value"),
            pl.col("_distinct_count").cast(pl.Float64).alias("numerator"),
            null_float.alias("denominator"),
            pl.col("_row_count").cast(pl.Int64).alias("sample_size"),
        ]
    if calculation == "sum":
        return [
            pl.col("_value_sum").cast(pl.Float64).alias("value"),
            pl.col("_value_sum").cast(pl.Float64).alias("numerator"),
            null_float.alias("denominator"),
            pl.col("_value_count").cast(pl.Int64).alias("sample_size"),
        ]
    if calculation == "mean":
        value = (
            pl.when(pl.col("_value_count") > 0)
            .then(pl.col("_value_sum") / pl.col("_value_count"))
            .otherwise(None)
        )
        return [
            value.cast(pl.Float64).alias("value"),
            pl.col("_value_sum").cast(pl.Float64).alias("numerator"),
            pl.col("_value_count").cast(pl.Float64).alias("denominator"),
            pl.col("_value_count").cast(pl.Int64).alias("sample_size"),
        ]
    if calculation == "quantile":
        return [
            pl.col("_quantile").cast(pl.Float64).alias("value"),
            null_float.alias("numerator"),
            null_float.alias("denominator"),
            pl.col("_value_count").cast(pl.Int64).alias("sample_size"),
        ]
    if calculation in {"ratio", "ratio_of_sums"}:
        value = (
            pl.when(pl.col("_denominator") > 0)
            .then(pl.col("_numerator") / pl.col("_denominator"))
            .otherwise(None)
        )
        sample_size = (
            pl.col("_row_count").cast(pl.Int64)
            if calculation == "ratio_of_sums"
            else pl.col("_denominator").round(0).cast(pl.Int64)
        )
        return [
            value.cast(pl.Float64).alias("value"),
            pl.col("_numerator").cast(pl.Float64).alias("numerator"),
            pl.col("_denominator").cast(pl.Float64).alias("denominator"),
            sample_size.alias("sample_size"),
        ]
    raise AssertionError(f"unknown calculation: {calculation}")


def _quality_expression(definition: MetricDefinition, minimum_denominator: int) -> pl.Expr:
    if definition.calculation in {"ratio", "ratio_of_sums", "mean", "quantile"}:
        return (
            pl.when(pl.col("sample_size") <= 0)
            .then(pl.lit("undefined"))
            .when(pl.col("sample_size") < minimum_denominator)
            .then(pl.lit("insufficient_data"))
            .otherwise(pl.lit("ok"))
            .alias("quality_status")
        )
    return pl.lit("ok").alias("quality_status")


def _dimension_expressions(cohort: CohortSpec) -> list[pl.Expr]:
    enabled = set(cohort.dimensions)
    return [
        pl.col(name).cast(pl.String).alias(name)
        if name in enabled
        else pl.lit(None, dtype=pl.String).alias(name)
        for name in ANALYTIC_DIMENSIONS
    ]


def calculate_metric_observations(
    facts: FactMap,
    config: AnalyticsConfig,
) -> pl.DataFrame:
    """Calculate all configured metric, grain, and supported cohort observations."""

    outputs: list[pl.DataFrame] = []
    for metric_name in config.metric_names:
        definition = METRIC_REGISTRY[metric_name]
        fact = facts[definition.fact]
        if fact.is_empty():
            continue
        for grain in config.time_grains:
            periodized = fact.with_columns(period_expression(definition.timestamp_column, grain))
            for cohort in _supported_cohorts(definition, config.cohorts):
                group_columns = ["period_start", *cohort.dimensions]
                aggregated = periodized.group_by(group_columns, maintain_order=True).agg(
                    _aggregate_expressions(definition)
                )
                if aggregated.is_empty():
                    continue
                higher = (
                    pl.lit(definition.higher_is_better, dtype=pl.Boolean)
                    if definition.higher_is_better is not None
                    else pl.lit(None, dtype=pl.Boolean)
                )
                observation = (
                    aggregated.with_columns(_measurement_columns(definition))
                    .with_columns(
                        pl.lit(definition.name).alias("metric_name"),
                        pl.lit(definition.display_name).alias("display_name"),
                        pl.lit(definition.domain).alias("domain"),
                        pl.lit(definition.metric_type).alias("metric_type"),
                        pl.lit(definition.unit).alias("unit"),
                        higher.alias("higher_is_better"),
                        pl.lit(grain).alias("time_grain"),
                        (pl.col("period_start") + _GRAIN_DURATION[grain]).alias("period_end"),
                        pl.lit(cohort.name).alias("cohort_name"),
                        pl.lit(len(cohort.dimensions), dtype=pl.Int64).alias("dimension_count"),
                        *_dimension_expressions(cohort),
                    )
                    .with_columns(_quality_expression(definition, config.minimum_denominator))
                )
                outputs.append(conform_analytics_frame("metric_observations", observation))
    if not outputs:
        return empty_analytics_frame("metric_observations")
    return pl.concat(outputs, how="vertical").sort(
        [
            "metric_name",
            "time_grain",
            "period_start",
            "cohort_name",
            *ANALYTIC_DIMENSIONS,
        ],
        nulls_last=True,
    )

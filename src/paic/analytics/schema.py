"""Canonical schemas for analytical artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.simulator.schema import UTC_DATETIME


@dataclass(frozen=True)
class AnalyticsTableSpec:
    name: str
    columns: tuple[tuple[str, pl.DataType], ...]
    primary_key: tuple[str, ...]
    timestamp_columns: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return dict(self.columns)


_DIMENSION_COLUMNS: tuple[tuple[str, pl.DataType], ...] = tuple(
    (name, pl.String) for name in ANALYTIC_DIMENSIONS
)

ANALYTICS_TABLE_SPECS: dict[str, AnalyticsTableSpec] = {
    "metric_observations": AnalyticsTableSpec(
        name="metric_observations",
        columns=(
            ("metric_name", pl.String),
            ("display_name", pl.String),
            ("domain", pl.String),
            ("metric_type", pl.String),
            ("unit", pl.String),
            ("higher_is_better", pl.Boolean),
            ("time_grain", pl.String),
            ("period_start", UTC_DATETIME),
            ("period_end", UTC_DATETIME),
            ("cohort_name", pl.String),
            ("dimension_count", pl.Int64),
            *_DIMENSION_COLUMNS,
            ("value", pl.Float64),
            ("numerator", pl.Float64),
            ("denominator", pl.Float64),
            ("sample_size", pl.Int64),
            ("quality_status", pl.String),
        ),
        primary_key=(
            "metric_name",
            "time_grain",
            "period_start",
            "cohort_name",
            *ANALYTIC_DIMENSIONS,
        ),
        timestamp_columns=("period_start", "period_end"),
    ),
    "funnel_observations": AnalyticsTableSpec(
        name="funnel_observations",
        columns=(
            ("time_grain", pl.String),
            ("period_start", UTC_DATETIME),
            ("period_end", UTC_DATETIME),
            ("cohort_name", pl.String),
            ("dimension_count", pl.Int64),
            *_DIMENSION_COLUMNS,
            ("stage_order", pl.Int64),
            ("stage_name", pl.String),
            ("stage_count", pl.Int64),
            ("previous_stage_count", pl.Int64),
            ("conversion_from_previous", pl.Float64),
            ("conversion_from_start", pl.Float64),
            ("drop_off_count", pl.Int64),
            ("drop_off_rate", pl.Float64),
            ("quality_status", pl.String),
        ),
        primary_key=(
            "time_grain",
            "period_start",
            "cohort_name",
            *ANALYTIC_DIMENSIONS,
            "stage_name",
        ),
        timestamp_columns=("period_start", "period_end"),
    ),
    "contribution_observations": AnalyticsTableSpec(
        name="contribution_observations",
        columns=(
            ("analysis_name", pl.String),
            ("metric_name", pl.String),
            ("time_grain", pl.String),
            ("baseline_period_start", UTC_DATETIME),
            ("baseline_period_end", UTC_DATETIME),
            ("current_period_start", UTC_DATETIME),
            ("current_period_end", UTC_DATETIME),
            ("dimension_name", pl.String),
            ("cohort_value", pl.String),
            ("baseline_numerator", pl.Float64),
            ("baseline_denominator", pl.Float64),
            ("baseline_rate", pl.Float64),
            ("current_numerator", pl.Float64),
            ("current_denominator", pl.Float64),
            ("current_rate", pl.Float64),
            ("baseline_share", pl.Float64),
            ("current_share", pl.Float64),
            ("rate_effect", pl.Float64),
            ("mix_effect", pl.Float64),
            ("total_contribution", pl.Float64),
            ("overall_change", pl.Float64),
            ("contribution_share", pl.Float64),
            ("direction", pl.String),
            ("quality_status", pl.String),
        ),
        primary_key=(
            "analysis_name",
            "baseline_period_start",
            "current_period_start",
            "cohort_value",
        ),
        timestamp_columns=(
            "baseline_period_start",
            "baseline_period_end",
            "current_period_start",
            "current_period_end",
        ),
    ),
    "data_quality_results": AnalyticsTableSpec(
        name="data_quality_results",
        columns=(
            ("check_name", pl.String),
            ("category", pl.String),
            ("severity", pl.String),
            ("status", pl.String),
            ("metric_name", pl.String),
            ("time_grain", pl.String),
            ("period_start", UTC_DATETIME),
            ("cohort_name", pl.String),
            ("observed_value", pl.Float64),
            ("expected", pl.String),
            ("details", pl.String),
        ),
        primary_key=(
            "check_name",
            "metric_name",
            "time_grain",
            "period_start",
            "cohort_name",
        ),
        timestamp_columns=("period_start",),
    ),
}

ANALYTICS_TABLE_ORDER: tuple[str, ...] = tuple(ANALYTICS_TABLE_SPECS)


def empty_analytics_frame(table_name: str) -> pl.DataFrame:
    return pl.DataFrame(schema=ANALYTICS_TABLE_SPECS[table_name].schema)


def conform_analytics_frame(table_name: str, frame: pl.DataFrame) -> pl.DataFrame:
    spec = ANALYTICS_TABLE_SPECS[table_name]
    missing = [name for name, _ in spec.columns if name not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} is missing columns: {', '.join(missing)}")
    return frame.select(
        [pl.col(name).cast(dtype, strict=True).alias(name) for name, dtype in spec.columns]
    )

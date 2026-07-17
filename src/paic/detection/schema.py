"""Canonical schemas for anomaly-detection artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.simulator.schema import UTC_DATETIME


@dataclass(frozen=True)
class DetectionTableSpec:
    name: str
    columns: tuple[tuple[str, pl.DataType], ...]
    primary_key: tuple[str, ...]
    timestamp_columns: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return dict(self.columns)


_DIMENSIONS: tuple[tuple[str, pl.DataType], ...] = tuple(
    (name, pl.String) for name in ANALYTIC_DIMENSIONS
)

DETECTION_TABLE_SPECS: dict[str, DetectionTableSpec] = {
    "detector_observations": DetectionTableSpec(
        name="detector_observations",
        columns=(
            ("observation_id", pl.String),
            ("series_id", pl.String),
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
            *_DIMENSIONS,
            ("observed_value", pl.Float64),
            ("expected_value", pl.Float64),
            ("expected_lower", pl.Float64),
            ("expected_upper", pl.Float64),
            ("numerator", pl.Float64),
            ("denominator", pl.Float64),
            ("sample_size", pl.Int64),
            ("baseline_points", pl.Int64),
            ("seasonal_points", pl.Int64),
            ("baseline_method", pl.String),
            ("baseline_last_period", UTC_DATETIME),
            ("robust_scale", pl.Float64),
            ("residual", pl.Float64),
            ("absolute_change", pl.Float64),
            ("relative_change", pl.Float64),
            ("robust_z", pl.Float64),
            ("distribution_name", pl.String),
            ("p_value", pl.Float64),
            ("q_value", pl.Float64),
            ("cusum_score", pl.Float64),
            ("change_detected", pl.Boolean),
            ("estimated_change_start", UTC_DATETIME),
            ("sequential_log_likelihood", pl.Float64),
            ("sequential_alert", pl.Boolean),
            ("support_robust_deviation", pl.Boolean),
            ("support_fdr_significance", pl.Boolean),
            ("support_cusum", pl.Boolean),
            ("support_sequential", pl.Boolean),
            ("detector_support_count", pl.Int64),
            ("sample_size_gate_passed", pl.Boolean),
            ("effect_size_gate_passed", pl.Boolean),
            ("history_gate_passed", pl.Boolean),
            ("alert_reason_codes", pl.String),
            ("is_eligible", pl.Boolean),
            ("is_anomaly", pl.Boolean),
            ("direction", pl.String),
            ("impact_direction", pl.String),
            ("severity", pl.String),
            ("evidence_score", pl.Float64),
            ("source_quality_status", pl.String),
            ("scenario_id", pl.String),
        ),
        primary_key=("observation_id",),
        timestamp_columns=(
            "period_start",
            "period_end",
            "baseline_last_period",
            "estimated_change_start",
        ),
    ),
    "anomaly_events": DetectionTableSpec(
        name="anomaly_events",
        columns=(
            ("event_id", pl.String),
            ("series_id", pl.String),
            ("metric_name", pl.String),
            ("time_grain", pl.String),
            ("cohort_name", pl.String),
            *_DIMENSIONS,
            ("started_at", UTC_DATETIME),
            ("ended_at", UTC_DATETIME),
            ("first_detected_at", UTC_DATETIME),
            ("peak_at", UTC_DATETIME),
            ("observation_count", pl.Int64),
            ("max_evidence_score", pl.Float64),
            ("minimum_q_value", pl.Float64),
            ("max_absolute_z", pl.Float64),
            ("max_absolute_relative_change", pl.Float64),
            ("direction", pl.String),
            ("impact_direction", pl.String),
            ("severity", pl.String),
            ("scenario_ids", pl.String),
        ),
        primary_key=("event_id",),
        timestamp_columns=("started_at", "ended_at", "first_detected_at", "peak_at"),
    ),
    "change_point_events": DetectionTableSpec(
        name="change_point_events",
        columns=(
            ("change_event_id", pl.String),
            ("series_id", pl.String),
            ("metric_name", pl.String),
            ("time_grain", pl.String),
            ("cohort_name", pl.String),
            *_DIMENSIONS,
            ("detected_at", UTC_DATETIME),
            ("estimated_start_at", UTC_DATETIME),
            ("direction", pl.String),
            ("cusum_score", pl.Float64),
            ("robust_z", pl.Float64),
            ("scenario_id", pl.String),
        ),
        primary_key=("change_event_id",),
        timestamp_columns=("detected_at", "estimated_start_at"),
    ),
    "benchmark_ground_truth": DetectionTableSpec(
        name="benchmark_ground_truth",
        columns=(
            ("scenario_id", pl.String),
            ("metric_name", pl.String),
            ("time_grain", pl.String),
            ("cohort_name", pl.String),
            *_DIMENSIONS,
            ("started_at", UTC_DATETIME),
            ("ended_at", UTC_DATETIME),
            ("kind", pl.String),
            ("magnitude", pl.Float64),
            ("expected_direction", pl.String),
            ("injected_points", pl.Int64),
        ),
        primary_key=("scenario_id",),
        timestamp_columns=("started_at", "ended_at"),
    ),
    "benchmark_results": DetectionTableSpec(
        name="benchmark_results",
        columns=(
            ("scenario_id", pl.String),
            ("detected", pl.Boolean),
            ("first_detected_at", UTC_DATETIME),
            ("detection_delay_periods", pl.Int64),
            ("expected_direction", pl.String),
            ("direction_match", pl.Boolean),
            ("true_positive_points", pl.Int64),
            ("false_negative_points", pl.Int64),
            ("peak_evidence_score", pl.Float64),
            ("matched_event_id", pl.String),
        ),
        primary_key=("scenario_id",),
        timestamp_columns=("first_detected_at",),
    ),
    "benchmark_summary": DetectionTableSpec(
        name="benchmark_summary",
        columns=(
            ("scope", pl.String),
            ("scenario_count", pl.Int64),
            ("scenarios_detected", pl.Int64),
            ("observation_true_positives", pl.Int64),
            ("observation_false_positives", pl.Int64),
            ("observation_false_negatives", pl.Int64),
            ("eligible_non_scenario_points", pl.Int64),
            ("precision", pl.Float64),
            ("scenario_recall", pl.Float64),
            ("point_recall", pl.Float64),
            ("false_positive_rate", pl.Float64),
            ("mean_detection_delay_periods", pl.Float64),
            ("median_detection_delay_periods", pl.Float64),
        ),
        primary_key=("scope",),
    ),
    "detection_quality_results": DetectionTableSpec(
        name="detection_quality_results",
        columns=(
            ("check_name", pl.String),
            ("category", pl.String),
            ("severity", pl.String),
            ("status", pl.String),
            ("observed_value", pl.Float64),
            ("expected", pl.String),
            ("details", pl.String),
        ),
        primary_key=("check_name",),
    ),
}

DETECTION_TABLE_ORDER: tuple[str, ...] = tuple(DETECTION_TABLE_SPECS)


def empty_detection_frame(table_name: str) -> pl.DataFrame:
    return pl.DataFrame(schema=DETECTION_TABLE_SPECS[table_name].schema)


def conform_detection_frame(table_name: str, frame: pl.DataFrame) -> pl.DataFrame:
    spec = DETECTION_TABLE_SPECS[table_name]
    missing = [name for name, _ in spec.columns if name not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} is missing columns: {', '.join(missing)}")
    return frame.select(
        [pl.col(name).cast(dtype, strict=True).alias(name) for name, dtype in spec.columns]
    )

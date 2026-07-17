"""End-to-end deterministic anomaly-detection engine."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import cast

import polars as pl

from paic.analytics.config import TimeGrain
from paic.analytics.io import load_analytics
from paic.analytics.registry import ANALYTIC_DIMENSIONS, METRIC_REGISTRY
from paic.analytics.validation import validate_analytics_directory
from paic.detection.config import (
    AlertPolicy,
    BenchmarkScenario,
    DetectionConfig,
    MetricPolicyOverride,
)
from paic.detection.schema import conform_detection_frame, empty_detection_frame
from paic.detection.statistics import (
    benjamini_hochberg,
    distribution_p_value,
    evidence_score,
    robust_interval,
    robust_location_scale,
    severity_from_score,
)
from paic.detection.types import DetectionBuildResult
from paic.detection.utils import period_delta, stable_hash_id
from paic.simulator.io import file_sha256


class DetectionBuildError(RuntimeError):
    """Raised when analytical data cannot produce a valid detection artifact."""


@dataclass(frozen=True)
class HistoryPoint:
    period: datetime
    value: float
    numerator: float | None
    denominator: float | None


@dataclass
class CusumState:
    positive: float = 0.0
    negative: float = 0.0
    positive_start: datetime | None = None
    negative_start: datetime | None = None
    previously_above_threshold: bool = False


@dataclass
class SequentialState:
    positive: float = 0.0
    negative: float = 0.0
    previously_above_threshold: bool = False


_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    result = float(cast(float | int, value))
    return result if math.isfinite(result) else None


def _season_matches(
    current: datetime,
    previous: datetime,
    grain: TimeGrain,
    seasonal_periods: int,
) -> bool:
    """Return whether a prior point is aligned to the configured seasonal cycle."""

    cycle = seasonal_periods * period_delta(grain)
    elapsed = current - previous
    cycles = elapsed.total_seconds() / cycle.total_seconds()
    return elapsed >= cycle and cycles.is_integer()


def _direction(residual: float | None) -> str:
    if residual is None or abs(residual) <= 1e-15:
        return "flat"
    return "increase" if residual > 0 else "decrease"


def _impact_direction(direction: str, higher_is_better: bool | None) -> str:
    if direction == "flat" or higher_is_better is None:
        return "neutral"
    if (direction == "increase" and higher_is_better) or (
        direction == "decrease" and not higher_is_better
    ):
        return "improvement"
    return "degradation"


def _policy_values(
    base: AlertPolicy, override: MetricPolicyOverride | None
) -> tuple[float, int, float, float, int]:
    return (
        override.robust_z_threshold
        if override is not None and override.robust_z_threshold is not None
        else base.robust_z_threshold,
        override.minimum_detector_support
        if override is not None and override.minimum_detector_support is not None
        else base.minimum_detector_support,
        override.minimum_relative_effect
        if override is not None and override.minimum_relative_effect is not None
        else base.minimum_relative_effect,
        override.minimum_absolute_effect
        if override is not None and override.minimum_absolute_effect is not None
        else base.minimum_absolute_effect,
        override.minimum_sample_size
        if override is not None and override.minimum_sample_size is not None
        else base.minimum_sample_size,
    )


def _select_metric_observations(metrics: pl.DataFrame, config: DetectionConfig) -> pl.DataFrame:
    selected: list[pl.DataFrame] = []
    available_cohorts = set(metrics.get_column("cohort_name").unique().to_list())
    for selector in config.selectors:
        unknown = set(selector.cohorts).difference(available_cohorts)
        if unknown:
            raise DetectionBuildError(
                f"selector {selector.metric} references unavailable cohorts: {sorted(unknown)}"
            )
        definition = METRIC_REGISTRY[selector.metric]
        cohort_dimensions = (
            metrics.filter(pl.col("metric_name") == selector.metric)
            .select("cohort_name", *ANALYTIC_DIMENSIONS)
            .unique()
        )
        if cohort_dimensions.is_empty():
            raise DetectionBuildError(
                f"selected metric has no analytical observations: {selector.metric}"
            )
        part = metrics.filter(
            (pl.col("metric_name") == selector.metric)
            & pl.col("time_grain").is_in(selector.time_grains)
            & pl.col("cohort_name").is_in(selector.cohorts)
        )
        if part.is_empty():
            raise DetectionBuildError(
                f"selector matched no rows: {selector.metric}/{selector.time_grains}/{selector.cohorts}"
            )
        unsupported = set()
        for row in part.select("cohort_name", *ANALYTIC_DIMENSIONS).unique().iter_rows(named=True):
            dimensions = {name for name in ANALYTIC_DIMENSIONS if row[name] is not None}
            if not dimensions.issubset(definition.supported_dimensions):
                unsupported.update(dimensions.difference(definition.supported_dimensions))
        if unsupported:
            raise DetectionBuildError(
                f"metric {selector.metric} does not support dimensions: {sorted(unsupported)}"
            )
        selected.append(part)
    combined = pl.concat(selected, how="vertical").unique(
        subset=["metric_name", "time_grain", "period_start", "cohort_name", *ANALYTIC_DIMENSIONS],
        keep="first",
        maintain_order=True,
    )
    rows = combined.to_dicts()
    for row in rows:
        payload = {
            "metric": row["metric_name"],
            "grain": row["time_grain"],
            "cohort": row["cohort_name"],
            "dimensions": {
                name: row[name] for name in ANALYTIC_DIMENSIONS if row[name] is not None
            },
        }
        row["series_id"] = stable_hash_id("SER", payload)
    return pl.from_dicts(rows, infer_schema_length=None).sort(
        ["metric_name", "time_grain", "cohort_name", *ANALYTIC_DIMENSIONS, "period_start"],
        nulls_last=True,
    )


def _scenario_mask(row: dict[str, object], scenario: BenchmarkScenario) -> bool:
    period = cast(datetime, row["period_start"])
    end_at = scenario.start_at + scenario.duration_periods * period_delta(scenario.time_grain)
    return (
        row["metric_name"] == scenario.metric
        and row["time_grain"] == scenario.time_grain
        and row["cohort_name"] == scenario.cohort
        and scenario.start_at <= period < end_at
        and all(
            row.get(dimension) == value for dimension, value in scenario.dimension_values.items()
        )
    )


def _perturb(value: float, scenario: BenchmarkScenario, sequence: int) -> float:
    magnitude = float(scenario.magnitude)
    factor = (
        1.0 + magnitude * ((sequence + 1) / scenario.duration_periods)
        if scenario.kind == "drift"
        else 1.0 + magnitude
    )
    return value * factor


def _apply_benchmark_scenarios(
    metrics: pl.DataFrame, scenarios: list[BenchmarkScenario]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = metrics.with_columns(pl.lit(None, dtype=pl.String).alias("scenario_id")).to_dicts()
    if not scenarios:
        return pl.from_dicts(rows, infer_schema_length=None), empty_detection_frame(
            "benchmark_ground_truth"
        )
    occupied: set[int] = set()
    truth_rows: list[dict[str, object]] = []
    for scenario in scenarios:
        indices = [index for index, row in enumerate(rows) if _scenario_mask(row, scenario)]
        if len(indices) != scenario.duration_periods:
            raise DetectionBuildError(
                f"benchmark scenario {scenario.scenario_id} expected {scenario.duration_periods} "
                f"periods but matched {len(indices)}"
            )
        overlap = occupied.intersection(indices)
        if overlap:
            raise DetectionBuildError(
                f"benchmark scenario {scenario.scenario_id} overlaps another scenario"
            )
        occupied.update(indices)
        definition = METRIC_REGISTRY[scenario.metric]
        for sequence, index in enumerate(indices):
            row = rows[index]
            value = _safe_float(row.get("value"))
            if value is None:
                raise DetectionBuildError(
                    f"benchmark scenario {scenario.scenario_id} targets an undefined observation"
                )
            perturbed = _perturb(value, scenario, sequence)
            if definition.expected_min is not None:
                perturbed = max(perturbed, definition.expected_min)
            if definition.expected_max is not None:
                perturbed = min(perturbed, definition.expected_max)
            denominator = _safe_float(row.get("denominator"))
            if definition.metric_type == "ratio" and denominator is not None:
                trials = max(round(denominator), 0)
                successes = min(max(round(perturbed * trials), 0), trials)
                row["numerator"] = float(successes)
                row["value"] = float(successes / trials) if trials else None
            elif definition.calculation in {"count", "distinct_count"}:
                count_value = max(round(perturbed), 0)
                row["value"] = float(count_value)
                row["numerator"] = float(count_value)
                row["sample_size"] = count_value
            elif definition.calculation == "sum":
                row["value"] = max(float(perturbed), 0.0)
                row["numerator"] = row["value"]
            elif definition.calculation in {"mean", "ratio_of_sums"} and denominator is not None:
                row["value"] = float(perturbed)
                row["numerator"] = float(perturbed) * denominator
            else:
                row["value"] = float(perturbed)
            row["scenario_id"] = scenario.scenario_id
        dimensions: dict[str, object] = {
            name: scenario.dimension_values.get(name) for name in ANALYTIC_DIMENSIONS
        }
        truth_rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "metric_name": scenario.metric,
                "time_grain": scenario.time_grain,
                "cohort_name": scenario.cohort,
                **dimensions,
                "started_at": scenario.start_at,
                "ended_at": scenario.start_at
                + scenario.duration_periods * period_delta(scenario.time_grain),
                "kind": scenario.kind,
                "magnitude": scenario.magnitude,
                "expected_direction": scenario.expected_direction,
                "injected_points": len(indices),
            }
        )
    return (
        pl.from_dicts(rows, infer_schema_length=None).sort(
            ["metric_name", "time_grain", "cohort_name", *ANALYTIC_DIMENSIONS, "period_start"],
            nulls_last=True,
        ),
        conform_detection_frame(
            "benchmark_ground_truth", pl.from_dicts(truth_rows, infer_schema_length=None)
        ).sort("scenario_id"),
    )


def _update_cusum(
    state: CusumState, z_score: float, period: datetime, *, drift: float, threshold: float
) -> tuple[float, bool, datetime | None]:
    if state.positive <= 0:
        state.positive_start = period
    if state.negative <= 0:
        state.negative_start = period
    state.positive = max(0.0, state.positive + z_score - drift)
    state.negative = max(0.0, state.negative - z_score - drift)
    score = max(state.positive, state.negative)
    above = score >= threshold
    detected = above and not state.previously_above_threshold
    state.previously_above_threshold = above
    estimated = state.positive_start if state.positive >= state.negative else state.negative_start
    return score, detected, estimated if above else None


def _update_sequential(
    state: SequentialState, z_score: float, *, shift: float, threshold: float
) -> tuple[float, bool]:
    state.positive = max(0.0, state.positive + shift * z_score - shift * shift / 2.0)
    state.negative = max(0.0, state.negative - shift * z_score - shift * shift / 2.0)
    score = max(state.positive, state.negative)
    above = score >= threshold
    alert = above and not state.previously_above_threshold
    state.previously_above_threshold = above
    return score, alert


def _score_series(metrics: pl.DataFrame, config: DetectionConfig) -> pl.DataFrame:
    output: list[dict[str, object]] = []
    sequential_threshold = math.log((1.0 - config.sequential.alpha) / config.sequential.alpha)
    for partition in metrics.partition_by("series_id", as_dict=False, maintain_order=True):
        rows = partition.sort("period_start").to_dicts()
        if not rows:
            continue
        metric_name = str(rows[0]["metric_name"])
        definition = METRIC_REGISTRY[metric_name]
        grain = cast(TimeGrain, str(rows[0]["time_grain"]))
        baseline_config = config.baseline.for_grain(grain)
        z_threshold, minimum_support, minimum_relative, minimum_absolute, minimum_sample = (
            _policy_values(config.alert_policy, config.override_map.get(metric_name))
        )
        history: list[HistoryPoint] = []
        cusum = CusumState()
        sequential = SequentialState()
        for source in rows:
            current_period = cast(datetime, source["period_start"])
            observed = _safe_float(source.get("value"))
            finite_history = history[-baseline_config.lookback_periods :]
            seasonal = [
                point
                for point in finite_history
                if _season_matches(
                    current_period,
                    point.period,
                    grain,
                    baseline_config.seasonal_periods,
                )
            ]
            baseline = (
                seasonal
                if len(seasonal) >= baseline_config.minimum_seasonal_history
                else finite_history
            )
            baseline_method = (
                "seasonal-median-mad"
                if baseline is seasonal
                and len(seasonal) >= baseline_config.minimum_seasonal_history
                else "rolling-median-mad"
            )
            expected: float | None = None
            scale: float | None = None
            lower: float | None = None
            upper: float | None = None
            residual: float | None = None
            absolute_change: float | None = None
            relative_change: float | None = None
            robust_z: float | None = None
            distribution_name = "unavailable"
            p_value: float | None = None
            cusum_score = 0.0
            change_detected = False
            estimated_change_start: datetime | None = None
            sequential_score = 0.0
            sequential_alert = False
            source_quality = str(source.get("quality_status") or "undefined")
            sample_size = int(source.get("sample_size") or 0)
            eligible = (
                observed is not None
                and source_quality in {"ok", "insufficient_data"}
                and sample_size >= minimum_sample
                and len(finite_history) >= baseline_config.minimum_history
            )
            if observed is not None and baseline:
                expected, scale = robust_location_scale(
                    [point.value for point in baseline],
                    minimum_scale=baseline_config.minimum_scale,
                    relative_scale_floor=baseline_config.relative_scale_floor,
                )
                lower, upper = robust_interval(expected, scale, definition)
                residual = observed - expected
                absolute_change = abs(residual)
                relative_change = (
                    residual / abs(expected)
                    if abs(expected) > baseline_config.minimum_scale
                    else None
                )
                robust_z = residual / scale
            if eligible and observed is not None and robust_z is not None:
                ratio_history = [
                    point
                    for point in baseline
                    if point.numerator is not None and point.denominator is not None
                ]
                distribution_name, p_value = distribution_p_value(
                    definition,
                    observed=observed,
                    numerator=_safe_float(source.get("numerator")),
                    denominator=_safe_float(source.get("denominator")),
                    robust_z=robust_z,
                    history_values=[point.value for point in baseline],
                    history_numerators=[cast(float, point.numerator) for point in ratio_history],
                    history_denominators=[
                        cast(float, point.denominator) for point in ratio_history
                    ],
                )
                if config.cusum.enabled:
                    cusum_score, change_detected, estimated_change_start = _update_cusum(
                        cusum,
                        robust_z,
                        current_period,
                        drift=config.cusum.drift,
                        threshold=config.cusum.threshold,
                    )
                if config.sequential.enabled:
                    sequential_score, sequential_alert = _update_sequential(
                        sequential,
                        robust_z,
                        shift=config.sequential.alternative_standardized_shift,
                        threshold=sequential_threshold,
                    )
            output.append(
                {
                    "observation_id": stable_hash_id(
                        "OBS",
                        {"series": source["series_id"], "period": current_period.isoformat()},
                    ),
                    "series_id": source["series_id"],
                    "metric_name": metric_name,
                    "display_name": source["display_name"],
                    "domain": source["domain"],
                    "metric_type": source["metric_type"],
                    "unit": source["unit"],
                    "higher_is_better": source.get("higher_is_better"),
                    "time_grain": grain,
                    "period_start": current_period,
                    "period_end": source["period_end"],
                    "cohort_name": source["cohort_name"],
                    "dimension_count": source["dimension_count"],
                    **{name: source.get(name) for name in ANALYTIC_DIMENSIONS},
                    "observed_value": observed,
                    "expected_value": expected,
                    "expected_lower": lower,
                    "expected_upper": upper,
                    "numerator": _safe_float(source.get("numerator")),
                    "denominator": _safe_float(source.get("denominator")),
                    "sample_size": sample_size,
                    "baseline_points": len(finite_history),
                    "seasonal_points": len(seasonal),
                    "baseline_method": baseline_method,
                    "baseline_last_period": finite_history[-1].period if finite_history else None,
                    "robust_scale": scale,
                    "residual": residual,
                    "absolute_change": absolute_change,
                    "relative_change": relative_change,
                    "robust_z": robust_z,
                    "distribution_name": distribution_name,
                    "p_value": p_value,
                    "q_value": None,
                    "cusum_score": cusum_score,
                    "change_detected": change_detected,
                    "estimated_change_start": estimated_change_start,
                    "sequential_log_likelihood": sequential_score,
                    "sequential_alert": sequential_alert,
                    "detector_support_count": 0,
                    "is_eligible": eligible,
                    "is_anomaly": False,
                    "direction": _direction(residual),
                    "impact_direction": _impact_direction(
                        _direction(residual), cast(bool | None, source.get("higher_is_better"))
                    ),
                    "severity": "none",
                    "evidence_score": 0.0,
                    "source_quality_status": source_quality,
                    "scenario_id": source.get("scenario_id"),
                    "_z_threshold": z_threshold,
                    "_minimum_support": minimum_support,
                    "_minimum_relative": minimum_relative,
                    "_minimum_absolute": minimum_absolute,
                }
            )
            if observed is not None and source_quality != "invalid":
                history.append(
                    HistoryPoint(
                        period=current_period,
                        value=observed,
                        numerator=_safe_float(source.get("numerator")),
                        denominator=_safe_float(source.get("denominator")),
                    )
                )
    return pl.from_dicts(output, infer_schema_length=None).sort(
        ["time_grain", "period_start", "series_id"]
    )


def _apply_fdr(observations: pl.DataFrame) -> pl.DataFrame:
    rows = observations.to_dicts()
    groups: dict[tuple[str, datetime], list[int]] = {}
    for index, row in enumerate(rows):
        if bool(row["is_eligible"]) and row.get("p_value") is not None:
            groups.setdefault(
                (str(row["time_grain"]), cast(datetime, row["period_start"])), []
            ).append(index)
    for indices in groups.values():
        adjusted = benjamini_hochberg([float(rows[index]["p_value"]) for index in indices])
        for index, q_value in zip(indices, adjusted, strict=True):
            rows[index]["q_value"] = q_value
    return pl.from_dicts(rows, infer_schema_length=None).sort(
        ["time_grain", "period_start", "series_id"]
    )


def _finalize_alerts(observations: pl.DataFrame, config: DetectionConfig) -> pl.DataFrame:
    rows = observations.to_dicts()
    sequential_threshold = math.log((1.0 - config.sequential.alpha) / config.sequential.alpha)
    for row in rows:
        z_threshold = float(row.pop("_z_threshold"))
        minimum_support = int(row.pop("_minimum_support"))
        minimum_relative = float(row.pop("_minimum_relative"))
        minimum_absolute = float(row.pop("_minimum_absolute"))
        robust_z = _safe_float(row.get("robust_z"))
        q_value = _safe_float(row.get("q_value"))
        relative_change = _safe_float(row.get("relative_change"))
        absolute_change = _safe_float(row.get("absolute_change"))
        supports = (
            robust_z is not None and abs(robust_z) >= z_threshold,
            q_value is not None and q_value <= config.alert_policy.fdr_alpha,
            float(row["cusum_score"]) >= config.cusum.threshold,
            float(row["sequential_log_likelihood"]) >= sequential_threshold,
        )
        support_count = sum(int(value) for value in supports)
        magnitude = (relative_change is not None and abs(relative_change) >= minimum_relative) or (
            minimum_absolute > 0
            and absolute_change is not None
            and absolute_change >= minimum_absolute
        )
        anomaly = (
            bool(row["is_eligible"])
            and bool(supports[1])
            and magnitude
            and support_count >= minimum_support
        )
        score = evidence_score(
            robust_z=robust_z,
            q_value=q_value,
            cusum_score=float(row["cusum_score"]),
            cusum_threshold=config.cusum.threshold,
            sequential_log_likelihood=float(row["sequential_log_likelihood"]),
            sequential_threshold=sequential_threshold,
            relative_change=relative_change,
            minimum_relative_effect=minimum_relative,
        )
        row["detector_support_count"] = support_count
        row["is_anomaly"] = anomaly
        row["evidence_score"] = score
        row["severity"] = severity_from_score(score, support_count) if anomaly else "none"
    return conform_detection_frame(
        "detector_observations", pl.from_dicts(rows, infer_schema_length=None)
    ).sort(["time_grain", "period_start", "series_id"])


def _build_events(observations: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    anomalies = observations.filter(pl.col("is_anomaly")).sort(["series_id", "period_start"])
    event_rows: list[dict[str, object]] = []
    for partition in anomalies.partition_by("series_id", as_dict=False, maintain_order=True):
        groups: list[list[dict[str, object]]] = []
        current: list[dict[str, object]] = []
        for row in partition.to_dicts():
            if current and cast(datetime, row["period_start"]) > cast(
                datetime, current[-1]["period_end"]
            ):
                groups.append(current)
                current = []
            current.append(row)
        if current:
            groups.append(current)
        for group in groups:
            peak = max(group, key=lambda item: float(cast(float | int, item["evidence_score"])))
            scenarios = sorted({str(item["scenario_id"]) for item in group if item["scenario_id"]})
            event_rows.append(
                {
                    "event_id": stable_hash_id(
                        "EVT",
                        {"series": group[0]["series_id"], "start": group[0]["period_start"]},
                    ),
                    "series_id": group[0]["series_id"],
                    "metric_name": group[0]["metric_name"],
                    "time_grain": group[0]["time_grain"],
                    "cohort_name": group[0]["cohort_name"],
                    **{name: group[0].get(name) for name in ANALYTIC_DIMENSIONS},
                    "started_at": group[0]["period_start"],
                    "ended_at": group[-1]["period_end"],
                    "first_detected_at": group[0]["period_end"],
                    "peak_at": peak["period_start"],
                    "observation_count": len(group),
                    "max_evidence_score": max(
                        float(cast(float | int, item["evidence_score"])) for item in group
                    ),
                    "minimum_q_value": min(
                        float(cast(float | int, item["q_value"]))
                        for item in group
                        if item["q_value"] is not None
                    ),
                    "max_absolute_z": max(
                        abs(float(cast(float | int, item["robust_z"]))) for item in group
                    ),
                    "max_absolute_relative_change": max(
                        abs(float(cast(float | int, item["relative_change"])))
                        for item in group
                        if item["relative_change"] is not None
                    ),
                    "direction": peak["direction"],
                    "impact_direction": peak["impact_direction"],
                    "severity": max(
                        (str(item["severity"]) for item in group), key=_SEVERITY_RANK.__getitem__
                    ),
                    "scenario_ids": json.dumps(scenarios, separators=(",", ":")),
                }
            )
    events = (
        conform_detection_frame(
            "anomaly_events", pl.from_dicts(event_rows, infer_schema_length=None)
        ).sort("started_at")
        if event_rows
        else empty_detection_frame("anomaly_events")
    )
    change_rows: list[dict[str, object]] = []
    for row in observations.filter(pl.col("change_detected")).to_dicts():
        change_rows.append(
            {
                "change_event_id": stable_hash_id(
                    "CHG", {"series": row["series_id"], "detected": row["period_end"]}
                ),
                "series_id": row["series_id"],
                "metric_name": row["metric_name"],
                "time_grain": row["time_grain"],
                "cohort_name": row["cohort_name"],
                **{name: row.get(name) for name in ANALYTIC_DIMENSIONS},
                "detected_at": row["period_end"],
                "estimated_start_at": row["estimated_change_start"],
                "direction": row["direction"],
                "cusum_score": row["cusum_score"],
                "robust_z": row["robust_z"],
                "scenario_id": row["scenario_id"],
            }
        )
    changes = (
        conform_detection_frame(
            "change_point_events", pl.from_dicts(change_rows, infer_schema_length=None)
        ).sort("detected_at")
        if change_rows
        else empty_detection_frame("change_point_events")
    )
    return events, changes


def _evaluate_benchmark(
    truth: pl.DataFrame, observations: pl.DataFrame, events: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if truth.is_empty():
        return empty_detection_frame("benchmark_results"), empty_detection_frame(
            "benchmark_summary"
        )
    result_rows: list[dict[str, object]] = []
    for scenario in truth.to_dicts():
        scenario_id = str(scenario["scenario_id"])
        target = observations.filter(pl.col("scenario_id") == scenario_id)
        alerts = target.filter(pl.col("is_anomaly")).sort("period_start")
        detected = not alerts.is_empty()
        first_detected = alerts.get_column("period_start").min() if detected else None
        grain = cast(TimeGrain, str(scenario["time_grain"]))
        delay = (
            int(
                (cast(datetime, first_detected) - cast(datetime, scenario["started_at"]))
                / period_delta(grain)
            )
            if first_detected is not None
            else None
        )
        direction_match = (
            bool(alerts.get_column("direction").eq(scenario["expected_direction"]).any())
            if detected
            else False
        )
        matched_event = None
        if detected and not events.is_empty():
            candidates = events.filter(
                (pl.col("series_id") == alerts.get_column("series_id").first())
                & (pl.col("started_at") < scenario["ended_at"])
                & (pl.col("ended_at") > scenario["started_at"])
            )
            if not candidates.is_empty():
                matched_event = candidates.sort("started_at").get_column("event_id").first()
        result_rows.append(
            {
                "scenario_id": scenario_id,
                "detected": detected,
                "first_detected_at": first_detected,
                "detection_delay_periods": delay,
                "expected_direction": scenario["expected_direction"],
                "direction_match": direction_match,
                "true_positive_points": alerts.height,
                "false_negative_points": target.height - alerts.height,
                "peak_evidence_score": alerts.get_column("evidence_score").max()
                if detected
                else None,
                "matched_event_id": matched_event,
            }
        )
    results = conform_detection_frame(
        "benchmark_results", pl.from_dicts(result_rows, infer_schema_length=None)
    ).sort("scenario_id")
    true_positives = int(results.get_column("true_positive_points").sum())
    false_negatives = int(results.get_column("false_negative_points").sum())
    false_positives = observations.filter(
        pl.col("is_anomaly") & pl.col("scenario_id").is_null()
    ).height
    eligible_non_scenario = observations.filter(
        pl.col("is_eligible") & pl.col("scenario_id").is_null()
    ).height
    scenarios_detected = int(results.get_column("detected").sum())
    delays = [
        int(value) for value in results.get_column("detection_delay_periods").drop_nulls().to_list()
    ]
    summary = conform_detection_frame(
        "benchmark_summary",
        pl.from_dicts(
            [
                {
                    "scope": "overall",
                    "scenario_count": truth.height,
                    "scenarios_detected": scenarios_detected,
                    "observation_true_positives": true_positives,
                    "observation_false_positives": false_positives,
                    "observation_false_negatives": false_negatives,
                    "eligible_non_scenario_points": eligible_non_scenario,
                    "precision": true_positives / (true_positives + false_positives)
                    if true_positives + false_positives
                    else 0.0,
                    "scenario_recall": scenarios_detected / truth.height,
                    "point_recall": true_positives / (true_positives + false_negatives)
                    if true_positives + false_negatives
                    else 0.0,
                    "false_positive_rate": false_positives / eligible_non_scenario
                    if eligible_non_scenario
                    else 0.0,
                    "mean_detection_delay_periods": mean(delays) if delays else None,
                    "median_detection_delay_periods": median(delays) if delays else None,
                }
            ],
            infer_schema_length=None,
        ),
    )
    return results, summary


def _quality_row(
    name: str,
    category: str,
    *,
    status: str,
    observed: float,
    expected: str,
    details: str,
    severity: str = "error",
) -> dict[str, object]:
    return {
        "check_name": name,
        "category": category,
        "severity": severity,
        "status": status,
        "observed_value": observed,
        "expected": expected,
        "details": details,
    }


def _build_quality(
    observations: pl.DataFrame,
    events: pl.DataFrame,
    truth: pl.DataFrame,
    results: pl.DataFrame,
    summary: pl.DataFrame,
    source_quality_errors: int,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    rows.append(
        _quality_row(
            "source.analytics_quality",
            "source",
            status="pass" if source_quality_errors == 0 else "fail",
            observed=float(source_quality_errors),
            expected="0 source analytical quality errors",
            details="the detector refuses to conceal upstream analytical failures",
        )
    )
    duplicates = observations.select("observation_id").is_duplicated().sum()
    rows.append(
        _quality_row(
            "observations.primary_key",
            "observations",
            status="pass" if duplicates == 0 else "fail",
            observed=float(duplicates),
            expected="0 duplicate observation IDs",
            details="observation IDs must be stable and unique",
        )
    )
    invalid_probabilities = observations.filter(
        (pl.col("p_value").is_not_null() & ~pl.col("p_value").is_between(0.0, 1.0, closed="both"))
        | (pl.col("q_value").is_not_null() & ~pl.col("q_value").is_between(0.0, 1.0, closed="both"))
    ).height
    rows.append(
        _quality_row(
            "observations.probability_bounds",
            "statistics",
            status="pass" if invalid_probabilities == 0 else "fail",
            observed=float(invalid_probabilities),
            expected="all p-values and q-values in [0, 1]",
            details="probability outputs must be mathematically bounded",
        )
    )
    lookahead = observations.filter(
        pl.col("baseline_last_period").is_not_null()
        & (pl.col("baseline_last_period") >= pl.col("period_start"))
    ).height
    rows.append(
        _quality_row(
            "baseline.no_lookahead",
            "statistics",
            status="pass" if lookahead == 0 else "fail",
            observed=float(lookahead),
            expected="0 baselines using current or future periods",
            details="every score must be computed only from prior observations",
        )
    )
    invalid_alerts = observations.filter(
        pl.col("is_anomaly")
        & (
            ~pl.col("is_eligible")
            | pl.col("q_value").is_null()
            | (pl.col("detector_support_count") < 1)
            | (pl.col("severity") == "none")
        )
    ).height
    rows.append(
        _quality_row(
            "alerts.policy_invariants",
            "alerts",
            status="pass" if invalid_alerts == 0 else "fail",
            observed=float(invalid_alerts),
            expected="all alerts eligible, FDR-scored, supported, and severe",
            details="alert records must satisfy deterministic policy invariants",
        )
    )
    event_invalid = events.filter(
        (pl.col("started_at") >= pl.col("ended_at"))
        | (pl.col("peak_at") < pl.col("started_at"))
        | (pl.col("peak_at") >= pl.col("ended_at"))
        | (pl.col("first_detected_at") < pl.col("started_at"))
        | (pl.col("observation_count") <= 0)
    ).height
    rows.append(
        _quality_row(
            "events.temporal_integrity",
            "events",
            status="pass" if event_invalid == 0 else "fail",
            observed=float(event_invalid),
            expected="0 temporally invalid anomaly events",
            details="event boundaries, peaks, and first detections must be ordered",
        )
    )
    truth_invalid = 0
    if not truth.is_empty():
        truth_invalid = truth.filter(
            (pl.col("started_at") >= pl.col("ended_at")) | (pl.col("injected_points") <= 0)
        ).height
    rows.append(
        _quality_row(
            "benchmark.ground_truth",
            "benchmark",
            status="pass" if truth_invalid == 0 else "fail",
            observed=float(truth_invalid),
            expected="valid non-empty benchmark windows",
            details="ground truth must be deterministic and temporally coherent",
        )
    )
    result_invalid = 0
    if not results.is_empty():
        result_invalid = results.filter(
            (pl.col("true_positive_points") < 0)
            | (pl.col("false_negative_points") < 0)
            | (pl.col("detected") & pl.col("first_detected_at").is_null())
            | (~pl.col("detected") & pl.col("first_detected_at").is_not_null())
        ).height
    rows.append(
        _quality_row(
            "benchmark.results",
            "benchmark",
            status="pass" if result_invalid == 0 else "fail",
            observed=float(result_invalid),
            expected="arithmetically valid per-scenario results",
            details="detection flags and timestamps must agree",
        )
    )
    summary_invalid = 0
    if not summary.is_empty():
        item = summary.to_dicts()[0]
        for key in ("precision", "scenario_recall", "point_recall", "false_positive_rate"):
            value = float(item[key])
            if not 0.0 <= value <= 1.0:
                summary_invalid += 1
    elif not truth.is_empty():
        summary_invalid = 1
    rows.append(
        _quality_row(
            "benchmark.summary",
            "benchmark",
            status="pass" if summary_invalid == 0 else "fail",
            observed=float(summary_invalid),
            expected="benchmark rates bounded in [0, 1]",
            details="aggregate benchmark metrics must be arithmetically valid",
        )
    )
    finite_invalid = observations.filter(
        pl.col("observed_value").is_nan()
        | pl.col("observed_value").is_infinite()
        | (
            pl.col("expected_value").is_not_null()
            & (pl.col("expected_value").is_nan() | pl.col("expected_value").is_infinite())
        )
    ).height
    rows.append(
        _quality_row(
            "observations.finite_values",
            "statistics",
            status="pass" if finite_invalid == 0 else "fail",
            observed=float(finite_invalid),
            expected="0 NaN or infinite observed/expected values",
            details="detector output must remain numerically finite",
        )
    )
    return conform_detection_frame(
        "detection_quality_results", pl.from_dicts(rows, infer_schema_length=None)
    ).sort("check_name")


def detection_quality_error_count(frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    return int(frame.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).height)


def build_detection(analytics_dir: str | Path, config: DetectionConfig) -> DetectionBuildResult:
    """Build baselines, detector scores, events, and benchmark evidence."""

    try:
        loaded = load_analytics(analytics_dir)
    except Exception as exc:
        raise DetectionBuildError(f"cannot load analytical artifact: {exc}") from exc
    if loaded.manifest.quality_error_count:
        raise DetectionBuildError(
            f"analytical artifact has {loaded.manifest.quality_error_count} quality errors"
        )
    source_validation = validate_analytics_directory(analytics_dir)
    if not source_validation.valid:
        details = [f"{item.code}: {item.message}" for item in source_validation.issues]
        raise DetectionBuildError(f"analytical artifact validation failed: {details}")
    selected = _select_metric_observations(loaded.tables["metric_observations"], config)
    injected, truth = _apply_benchmark_scenarios(selected, config.benchmark_scenarios)
    raw_scores = _score_series(injected, config)
    observations = _finalize_alerts(_apply_fdr(raw_scores), config)
    events, changes = _build_events(observations)
    benchmark_results, benchmark_summary = _evaluate_benchmark(truth, observations, events)
    quality = _build_quality(
        observations,
        events,
        truth,
        benchmark_results,
        benchmark_summary,
        loaded.manifest.quality_error_count,
    )
    if detection_quality_error_count(quality):
        failures = quality.filter(pl.col("status") == "fail").select("check_name", "details")
        raise DetectionBuildError(f"detection quality failed: {failures.to_dicts()}")
    return DetectionBuildResult(
        config=config,
        source_manifest=loaded.manifest,
        source_manifest_sha256=file_sha256(Path(analytics_dir) / "manifest.json"),
        tables={
            "detector_observations": observations,
            "anomaly_events": events,
            "change_point_events": changes,
            "benchmark_ground_truth": truth,
            "benchmark_results": benchmark_results,
            "benchmark_summary": benchmark_summary,
            "detection_quality_results": quality,
        },
    )

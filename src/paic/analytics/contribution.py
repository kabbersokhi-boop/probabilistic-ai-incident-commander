"""Exact adjacent-period decomposition for cohort rate and mix effects."""

from __future__ import annotations

import math
from itertools import pairwise

import polars as pl

from paic.analytics.config import AnalyticsConfig, ContributionSpec
from paic.analytics.schema import conform_analytics_frame, empty_analytics_frame


def _safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _period_rows(
    frame: pl.DataFrame,
    dimension: str,
    period: object,
) -> dict[str, tuple[float, float]]:
    selected = frame.filter(pl.col("period_start") == period).select(
        pl.col(dimension).cast(pl.String), "numerator", "denominator"
    )
    rows: dict[str, tuple[float, float]] = {}
    for item in selected.iter_rows(named=True):
        value = item[dimension]
        if value is None:
            continue
        numerator = float(item["numerator"] or 0.0)
        denominator = float(item["denominator"] or 0.0)
        rows[str(value)] = (numerator, denominator)
    return rows


def _decompose_analysis(
    observations: pl.DataFrame,
    config: AnalyticsConfig,
    analysis: ContributionSpec,
) -> list[dict[str, object]]:
    cohort = next(item for item in config.cohorts if item.dimensions == [analysis.dimension])
    frame = observations.filter(
        (pl.col("metric_name") == analysis.metric)
        & (pl.col("time_grain") == analysis.time_grain)
        & (pl.col("cohort_name") == cohort.name)
    )
    periods = frame.get_column("period_start").unique().sort().to_list()
    output: list[dict[str, object]] = []
    for baseline_period, current_period in pairwise(periods):
        baseline_rows = _period_rows(frame, analysis.dimension, baseline_period)
        current_rows = _period_rows(frame, analysis.dimension, current_period)
        cohort_values = sorted(set(baseline_rows) | set(current_rows))
        baseline_denominator = sum(value[1] for value in baseline_rows.values())
        current_denominator = sum(value[1] for value in current_rows.values())
        if baseline_denominator <= 0 or current_denominator <= 0:
            continue
        baseline_overall = _safe_rate(
            sum(value[0] for value in baseline_rows.values()), baseline_denominator
        )
        current_overall = _safe_rate(
            sum(value[0] for value in current_rows.values()), current_denominator
        )
        overall_change = current_overall - baseline_overall
        period_end = {
            row["period_start"]: row["period_end"]
            for row in frame.select("period_start", "period_end").unique().iter_rows(named=True)
        }
        rows_for_pair: list[dict[str, object]] = []
        for cohort_value in cohort_values:
            baseline_numerator, baseline_cohort_denominator = baseline_rows.get(
                cohort_value, (0.0, 0.0)
            )
            current_numerator, current_cohort_denominator = current_rows.get(
                cohort_value, (0.0, 0.0)
            )
            baseline_rate = _safe_rate(baseline_numerator, baseline_cohort_denominator)
            current_rate = _safe_rate(current_numerator, current_cohort_denominator)
            baseline_share = (
                baseline_cohort_denominator / baseline_denominator
                if baseline_denominator > 0
                else 0.0
            )
            current_share = (
                current_cohort_denominator / current_denominator if current_denominator > 0 else 0.0
            )
            rate_effect = 0.5 * (baseline_share + current_share) * (current_rate - baseline_rate)
            mix_effect = 0.5 * (baseline_rate + current_rate) * (current_share - baseline_share)
            total = rate_effect + mix_effect
            if total > 1e-15:
                direction = "positive"
            elif total < -1e-15:
                direction = "negative"
            else:
                direction = "neutral"
            quality = (
                "ok"
                if baseline_cohort_denominator >= config.minimum_denominator
                and current_cohort_denominator >= config.minimum_denominator
                else "insufficient_data"
            )
            rows_for_pair.append(
                {
                    "analysis_name": analysis.name,
                    "metric_name": analysis.metric,
                    "time_grain": analysis.time_grain,
                    "baseline_period_start": baseline_period,
                    "baseline_period_end": period_end[baseline_period],
                    "current_period_start": current_period,
                    "current_period_end": period_end[current_period],
                    "dimension_name": analysis.dimension,
                    "cohort_value": cohort_value,
                    "baseline_numerator": baseline_numerator,
                    "baseline_denominator": baseline_cohort_denominator,
                    "baseline_rate": baseline_rate,
                    "current_numerator": current_numerator,
                    "current_denominator": current_cohort_denominator,
                    "current_rate": current_rate,
                    "baseline_share": baseline_share,
                    "current_share": current_share,
                    "rate_effect": rate_effect,
                    "mix_effect": mix_effect,
                    "total_contribution": total,
                    "overall_change": overall_change,
                    "contribution_share": (
                        total / overall_change
                        if not math.isclose(overall_change, 0.0, abs_tol=1e-15)
                        else None
                    ),
                    "direction": direction,
                    "quality_status": quality,
                }
            )
        if rows_for_pair:
            reconstructed = 0.0
            for row in rows_for_pair:
                value = row["total_contribution"]
                if not isinstance(value, (int, float)):
                    raise TypeError("total_contribution must be numeric")
                reconstructed += float(value)
            if not math.isclose(reconstructed, overall_change, rel_tol=1e-9, abs_tol=1e-12):
                raise RuntimeError(
                    f"contribution decomposition failed for {analysis.name}: "
                    f"{reconstructed} != {overall_change}"
                )
            output.extend(rows_for_pair)
    return output


def calculate_contribution_observations(
    metric_observations: pl.DataFrame,
    config: AnalyticsConfig,
) -> pl.DataFrame:
    """Calculate exact Kitagawa-style rate and population-mix effects."""

    rows: list[dict[str, object]] = []
    for analysis in config.contributions:
        rows.extend(_decompose_analysis(metric_observations, config, analysis))
    if not rows:
        return empty_analytics_frame("contribution_observations")
    frame = pl.DataFrame(rows)
    return conform_analytics_frame("contribution_observations", frame).sort(
        ["analysis_name", "current_period_start", "cohort_value"]
    )

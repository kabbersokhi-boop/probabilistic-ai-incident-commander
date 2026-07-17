from __future__ import annotations

from pathlib import Path

import polars as pl

from paic.analytics.quality import (
    build_data_quality_results,
    quality_error_count,
    quality_summary,
)
from paic.analytics.types import AnalyticsBuildResult
from paic.simulator.io import load_dataset
from paic.simulator.validation import validate_dataset_directory


def test_normal_build_has_no_failed_error_checks(
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    quality = analytics_smoke_result.tables["data_quality_results"]
    summary = quality_summary(quality)
    assert quality_error_count(quality) == 0
    assert summary["failed"] == 0
    assert summary["passed"] > 100
    assert summary["warnings"] >= 1
    assert quality.filter(pl.col("status") == "fail").is_empty()


def test_quality_checks_detect_out_of_range_and_arithmetic_tampering(
    smoke_dataset_dir: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    _, source_tables = load_dataset(smoke_dataset_dir)
    metrics = analytics_smoke_result.tables["metric_observations"].with_columns(
        pl.when(
            (pl.col("metric_name") == "checkout_conversion_rate")
            & (pl.col("cohort_name") == "overall")
        )
        .then(pl.lit(2.0))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    quality = build_data_quality_results(
        validate_dataset_directory(smoke_dataset_dir),
        source_tables,
        analytics_smoke_result.facts,
        metrics,
        analytics_smoke_result.tables["funnel_observations"],
        analytics_smoke_result.tables["contribution_observations"],
        analytics_smoke_result.config,
    )
    failures = set(quality.filter(pl.col("status") == "fail").get_column("check_name"))
    assert "metrics.checkout_conversion_rate.expected_range" in failures
    assert "metrics.checkout_conversion_rate.arithmetic" in failures
    assert quality_error_count(quality) >= 2


def test_quality_checks_detect_fact_cardinality_drift(
    smoke_dataset_dir: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    _, source_tables = load_dataset(smoke_dataset_dir)
    facts = dict(analytics_smoke_result.facts)
    facts["checkout"] = facts["checkout"].head(facts["checkout"].height - 1)
    quality = build_data_quality_results(
        validate_dataset_directory(smoke_dataset_dir),
        source_tables,
        facts,
        analytics_smoke_result.tables["metric_observations"],
        analytics_smoke_result.tables["funnel_observations"],
        analytics_smoke_result.tables["contribution_observations"],
        analytics_smoke_result.config,
    )
    failure = quality.filter(pl.col("check_name") == "fact.checkout.cardinality")
    assert failure.get_column("status").to_list() == ["fail"]


def test_contribution_quality_groups_each_analysis_independently() -> None:
    from datetime import UTC, datetime, timedelta

    from paic.analytics.quality import _contribution_checks
    from paic.analytics.schema import conform_analytics_frame

    baseline = datetime(2026, 5, 1, tzinfo=UTC)
    current = baseline + timedelta(days=1)
    rows: list[dict[str, object]] = []
    for analysis_name, metric_name in (
        ("checkout-conversion-by-region", "checkout_conversion_rate"),
        ("payment-approval-by-issuer", "payment_approval_rate"),
    ):
        for cohort_value, contribution in (("A", 0.04), ("B", 0.06)):
            rows.append(
                {
                    "analysis_name": analysis_name,
                    "metric_name": metric_name,
                    "time_grain": "day",
                    "baseline_period_start": baseline,
                    "baseline_period_end": current,
                    "current_period_start": current,
                    "current_period_end": current + timedelta(days=1),
                    "dimension_name": "region",
                    "cohort_value": cohort_value,
                    "baseline_numerator": 40.0,
                    "baseline_denominator": 100.0,
                    "baseline_rate": 0.4,
                    "current_numerator": 50.0,
                    "current_denominator": 100.0,
                    "current_rate": 0.5,
                    "baseline_share": 0.5,
                    "current_share": 0.5,
                    "rate_effect": contribution,
                    "mix_effect": 0.0,
                    "total_contribution": contribution,
                    "overall_change": 0.1,
                    "contribution_share": contribution / 0.1,
                    "direction": "positive",
                    "quality_status": "ok",
                }
            )

    contributions = conform_analytics_frame(
        "contribution_observations", pl.from_dicts(rows, infer_schema_length=None)
    )
    checks = _contribution_checks(contributions)
    reconstruction = next(
        item for item in checks if item["check_name"] == "contribution.reconstruction"
    )
    assert reconstruction["status"] == "pass"
    assert reconstruction["observed_value"] == 0.0


def test_quality_fails_when_metric_is_missing_despite_nonempty_source(
    smoke_dataset_dir: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    _, source_tables = load_dataset(smoke_dataset_dir)
    metrics = analytics_smoke_result.tables["metric_observations"].filter(
        pl.col("metric_name") != "checkout_conversion_rate"
    )
    quality = build_data_quality_results(
        validate_dataset_directory(smoke_dataset_dir),
        source_tables,
        analytics_smoke_result.facts,
        metrics,
        analytics_smoke_result.tables["funnel_observations"],
        analytics_smoke_result.tables["contribution_observations"],
        analytics_smoke_result.config,
    )
    coverage = quality.filter(pl.col("check_name") == "metrics.coverage")
    assert coverage.get_column("status").to_list() == ["fail"]
    assert "checkout_conversion_rate" in coverage.get_column("details").item()


def test_quality_detects_funnel_formula_tampering(
    smoke_dataset_dir: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    _, source_tables = load_dataset(smoke_dataset_dir)
    funnel = analytics_smoke_result.tables["funnel_observations"].with_columns(
        pl.when(pl.col("stage_order") == 2)
        .then(pl.lit(0.5))
        .otherwise(pl.col("conversion_from_previous"))
        .alias("conversion_from_previous")
    )
    quality = build_data_quality_results(
        validate_dataset_directory(smoke_dataset_dir),
        source_tables,
        analytics_smoke_result.facts,
        analytics_smoke_result.tables["metric_observations"],
        funnel,
        analytics_smoke_result.tables["contribution_observations"],
        analytics_smoke_result.config,
    )
    check = quality.filter(pl.col("check_name") == "funnel.monotonicity_and_arithmetic")
    assert check.get_column("status").to_list() == ["fail"]


def test_quality_detects_missing_funnel_stage(
    smoke_dataset_dir: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    _, source_tables = load_dataset(smoke_dataset_dir)
    funnel = analytics_smoke_result.tables["funnel_observations"].filter(
        pl.col("stage_name") != "inventory_checked"
    )
    quality = build_data_quality_results(
        validate_dataset_directory(smoke_dataset_dir),
        source_tables,
        analytics_smoke_result.facts,
        analytics_smoke_result.tables["metric_observations"],
        funnel,
        analytics_smoke_result.tables["contribution_observations"],
        analytics_smoke_result.config,
    )
    check = quality.filter(pl.col("check_name") == "funnel.stage_layout")
    assert check.get_column("status").to_list() == ["fail"]

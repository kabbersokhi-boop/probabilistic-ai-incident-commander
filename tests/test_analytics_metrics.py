from __future__ import annotations

import math
from typing import cast

import polars as pl
import pytest

from paic.analytics.registry import ANALYTIC_DIMENSIONS, METRIC_REGISTRY
from paic.analytics.types import AnalyticsBuildResult
from paic.simulator.types import SimulationResult


def _overall_metric(result: AnalyticsBuildResult, metric_name: str) -> dict[str, object]:
    rows = (
        result.tables["metric_observations"]
        .filter(
            (pl.col("metric_name") == metric_name)
            & (pl.col("time_grain") == "day")
            & (pl.col("cohort_name") == "overall")
        )
        .to_dicts()
    )
    assert len(rows) == 1
    return cast(dict[str, object], rows[0])


def test_metric_registry_is_unique_and_declarative() -> None:
    assert len(METRIC_REGISTRY) == 43
    assert len(METRIC_REGISTRY) == len(set(METRIC_REGISTRY))
    for name, definition in METRIC_REGISTRY.items():
        assert name == definition.name
        assert definition.fact
        assert definition.timestamp_column
        assert set(definition.supported_dimensions).issubset(ANALYTIC_DIMENSIONS)
        if definition.calculation == "quantile":
            assert definition.quantile is not None
        if definition.calculation in {"ratio", "ratio_of_sums"}:
            assert definition.numerator_column is not None


def test_overall_metrics_recompute_exactly_from_raw_source(
    analytics_smoke_result: AnalyticsBuildResult,
    smoke_result: SimulationResult,
) -> None:
    tables = smoke_result.tables

    checkout = _overall_metric(analytics_smoke_result, "checkout_conversion_rate")
    assert checkout["numerator"] == pytest.approx(float(tables["orders"].height))
    assert checkout["denominator"] == pytest.approx(float(tables["checkout_sessions"].height))
    assert checkout["value"] == pytest.approx(
        tables["orders"].height / tables["checkout_sessions"].height
    )

    payment = _overall_metric(analytics_smoke_result, "payment_approval_rate")
    approved = int(tables["payment_attempts"].get_column("approved").sum())
    assert payment["numerator"] == pytest.approx(float(approved))
    assert payment["denominator"] == pytest.approx(float(tables["payment_attempts"].height))
    assert payment["value"] == pytest.approx(approved / tables["payment_attempts"].height)

    gross_value = _overall_metric(analytics_smoke_result, "gross_order_value")
    expected_gross = float(tables["orders"].get_column("total_amount").sum())
    assert gross_value["value"] == pytest.approx(expected_gross)
    assert gross_value["numerator"] == pytest.approx(expected_gross)
    assert gross_value["denominator"] is None

    average_order = _overall_metric(analytics_smoke_result, "average_order_value")
    assert average_order["numerator"] == pytest.approx(expected_gross)
    assert average_order["denominator"] == pytest.approx(float(tables["orders"].height))
    assert average_order["value"] == pytest.approx(expected_gross / tables["orders"].height)

    inventory = _overall_metric(analytics_smoke_result, "inventory_exact_match_rate")
    exact = int(
        (
            tables["inventory_snapshots"].get_column("feed_reported_quantity")
            == tables["inventory_snapshots"].get_column("available_quantity")
        ).sum()
    )
    assert inventory["numerator"] == pytest.approx(float(exact))
    assert inventory["denominator"] == pytest.approx(float(tables["inventory_snapshots"].height))

    pipeline = _overall_metric(analytics_smoke_result, "pipeline_row_retention_rate")
    inputs = int(tables["pipeline_runs"].get_column("input_rows").sum())
    outputs = int(tables["pipeline_runs"].get_column("output_rows").sum())
    assert pipeline["numerator"] == pytest.approx(float(outputs))
    assert pipeline["denominator"] == pytest.approx(float(inputs))
    assert pipeline["value"] == pytest.approx(outputs / inputs)
    assert pipeline["sample_size"] == tables["pipeline_runs"].height


def test_metric_observations_have_unique_keys_and_valid_arithmetic(
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    metrics = analytics_smoke_result.tables["metric_observations"]
    key = ["metric_name", "time_grain", "period_start", "cohort_name", *ANALYTIC_DIMENSIONS]
    assert not metrics.select(key).is_duplicated().any()
    assert metrics.filter(pl.col("quality_status") == "invalid").is_empty()
    assert metrics.filter(
        pl.col("value").is_not_null() & (pl.col("value").is_nan() | pl.col("value").is_infinite())
    ).is_empty()

    ratio_names = [
        name
        for name, definition in METRIC_REGISTRY.items()
        if definition.calculation in {"ratio", "ratio_of_sums", "mean"}
    ]
    ratios = metrics.filter(
        pl.col("metric_name").is_in(ratio_names) & pl.col("value").is_not_null()
    )
    max_error = ratios.select(
        (pl.col("value") - pl.col("numerator") / pl.col("denominator")).abs().max()
    ).item()
    assert float(max_error or 0.0) <= 1e-12


def test_supported_cohorts_reconcile_to_overall_totals(
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    metrics = analytics_smoke_result.tables["metric_observations"]
    metric_name = "checkout_conversion_rate"
    overall = metrics.filter(
        (pl.col("metric_name") == metric_name) & (pl.col("cohort_name") == "overall")
    )
    by_region = metrics.filter(
        (pl.col("metric_name") == metric_name) & (pl.col("cohort_name") == "region")
    )
    assert by_region.get_column("numerator").sum() == pytest.approx(
        overall.get_column("numerator").sum()
    )
    assert by_region.get_column("denominator").sum() == pytest.approx(
        overall.get_column("denominator").sum()
    )


def test_metric_values_obey_documented_bounds(
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    metrics = analytics_smoke_result.tables["metric_observations"]
    for metric_name in metrics.get_column("metric_name").unique().to_list():
        definition = METRIC_REGISTRY[str(metric_name)]
        values = (
            metrics.filter(pl.col("metric_name") == metric_name).get_column("value").drop_nulls()
        )
        assert all(math.isfinite(float(value)) for value in values)
        if definition.expected_min is not None:
            assert values.min() >= definition.expected_min - 1e-12
        if definition.expected_max is not None:
            assert values.max() <= definition.expected_max + 1e-12

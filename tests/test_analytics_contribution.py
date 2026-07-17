from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from paic.analytics.config import AnalyticsConfig
from paic.analytics.contribution import calculate_contribution_observations
from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.analytics.schema import conform_analytics_frame


def _metric_row(
    period: datetime,
    region: str,
    numerator: float,
    denominator: float,
) -> dict[str, object]:
    row: dict[str, object] = {
        "metric_name": "checkout_conversion_rate",
        "display_name": "Checkout conversion rate",
        "domain": "checkout",
        "metric_type": "ratio",
        "unit": "proportion",
        "higher_is_better": True,
        "time_grain": "day",
        "period_start": period,
        "period_end": period + timedelta(days=1),
        "cohort_name": "region",
        "dimension_count": 1,
        "value": numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
        "sample_size": int(denominator),
        "quality_status": "ok",
    }
    row.update({name: None for name in ANALYTIC_DIMENSIONS})
    row["region"] = region
    return row


def test_contribution_decomposition_exactly_reconstructs_rate_change(
    analytics_smoke_config: AnalyticsConfig,
) -> None:
    first = datetime(2026, 5, 1, tzinfo=UTC)
    second = first + timedelta(days=1)
    observations = conform_analytics_frame(
        "metric_observations",
        pl.from_dicts(
            [
                _metric_row(first, "A", 80.0, 100.0),
                _metric_row(first, "B", 20.0, 100.0),
                _metric_row(second, "A", 40.0, 50.0),
                _metric_row(second, "B", 90.0, 150.0),
            ],
            infer_schema_length=None,
        ),
    )
    output = calculate_contribution_observations(observations, analytics_smoke_config)
    checkout = output.filter(pl.col("analysis_name") == "checkout-conversion-by-region")

    assert checkout.height == 2
    overall_change = 0.65 - 0.50
    assert checkout.get_column("overall_change").unique().to_list() == pytest.approx(
        [overall_change]
    )
    assert checkout.get_column("total_contribution").sum() == pytest.approx(overall_change)
    assert checkout.get_column("baseline_share").sum() == pytest.approx(1.0)
    assert checkout.get_column("current_share").sum() == pytest.approx(1.0)
    assert (checkout.get_column("rate_effect") + checkout.get_column("mix_effect")).equals(
        checkout.get_column("total_contribution")
    )
    assert checkout.get_column("contribution_share").sum() == pytest.approx(1.0)


def test_contribution_skips_pairs_with_undefined_overall_denominator(
    analytics_smoke_config: AnalyticsConfig,
) -> None:
    first = datetime(2026, 5, 1, tzinfo=UTC)
    second = first + timedelta(days=1)
    rows = [
        _metric_row(first, "A", 0.0, 1.0),
        _metric_row(second, "A", 1.0, 1.0),
    ]
    rows[0]["denominator"] = 0.0
    rows[0]["value"] = None
    rows[0]["sample_size"] = 0
    observations = conform_analytics_frame(
        "metric_observations", pl.from_dicts(rows, infer_schema_length=None)
    )
    output = calculate_contribution_observations(observations, analytics_smoke_config)
    assert output.filter(pl.col("analysis_name") == "checkout-conversion-by-region").is_empty()

from __future__ import annotations

from itertools import pairwise

import polars as pl
import pytest

from paic.analytics.funnel import FUNNEL_STAGES
from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.analytics.types import AnalyticsBuildResult
from paic.simulator.types import SimulationResult


def test_overall_funnel_matches_source_and_is_monotonic(
    analytics_smoke_result: AnalyticsBuildResult,
    smoke_result: SimulationResult,
) -> None:
    funnel = (
        analytics_smoke_result.tables["funnel_observations"]
        .filter((pl.col("time_grain") == "day") & (pl.col("cohort_name") == "overall"))
        .sort("stage_order")
    )
    assert funnel.get_column("stage_name").to_list() == [name for name, _ in FUNNEL_STAGES]
    counts = [int(value) for value in funnel.get_column("stage_count").to_list()]
    assert counts[0] == smoke_result.tables["checkout_sessions"].height
    assert counts[-1] == smoke_result.tables["orders"].height
    assert all(current <= previous for previous, current in pairwise(counts))
    assert funnel.get_column("conversion_from_start")[0] == pytest.approx(1.0)
    assert funnel.get_column("conversion_from_start")[-1] == pytest.approx(counts[-1] / counts[0])


def test_funnel_arithmetic_and_primary_key_are_exact(
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    funnel = analytics_smoke_result.tables["funnel_observations"]
    key = ["time_grain", "period_start", "cohort_name", *ANALYTIC_DIMENSIONS, "stage_name"]
    assert not funnel.select(key).is_duplicated().any()
    assert funnel.filter(pl.col("stage_count") > pl.col("previous_stage_count")).is_empty()
    assert funnel.filter(
        pl.col("drop_off_count") != pl.col("previous_stage_count") - pl.col("stage_count")
    ).is_empty()

    comparable = funnel.filter(pl.col("previous_stage_count") > 0)
    previous_error = comparable.select(
        (
            pl.col("conversion_from_previous")
            - pl.col("stage_count") / pl.col("previous_stage_count")
        )
        .abs()
        .max()
    ).item()
    drop_error = comparable.select(
        (pl.col("drop_off_rate") - pl.col("drop_off_count") / pl.col("previous_stage_count"))
        .abs()
        .max()
    ).item()
    assert float(previous_error or 0.0) <= 1e-12
    assert float(drop_error or 0.0) <= 1e-12


def test_region_funnel_reconciles_to_overall(analytics_smoke_result: AnalyticsBuildResult) -> None:
    funnel = analytics_smoke_result.tables["funnel_observations"]
    overall = funnel.filter(pl.col("cohort_name") == "overall").select(
        "period_start", "stage_name", pl.col("stage_count").alias("overall_count")
    )
    by_region = (
        funnel.filter(pl.col("cohort_name") == "region")
        .group_by("period_start", "stage_name")
        .agg(pl.col("stage_count").sum().alias("cohort_count"))
    )
    compared = overall.join(by_region, on=["period_start", "stage_name"], how="inner")
    assert compared.height == len(FUNNEL_STAGES)
    assert compared.filter(pl.col("overall_count") != pl.col("cohort_count")).is_empty()

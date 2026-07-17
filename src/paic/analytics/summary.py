"""Human- and API-friendly summaries of analytical artifacts."""

from __future__ import annotations

from typing import Any, cast

import polars as pl

from paic.analytics.quality import quality_summary
from paic.analytics.types import LoadedAnalytics


def _latest_overall_metrics(metrics: pl.DataFrame) -> list[dict[str, Any]]:
    daily = metrics.filter((pl.col("time_grain") == "day") & (pl.col("cohort_name") == "overall"))
    if daily.is_empty():
        return []
    latest = daily.get_column("period_start").max()
    rows = (
        daily.filter(pl.col("period_start") == latest)
        .select("metric_name", "value", "unit", "sample_size", "quality_status")
        .sort("metric_name")
        .to_dicts()
    )
    return cast(list[dict[str, Any]], rows)


def _latest_overall_funnel(funnel: pl.DataFrame) -> list[dict[str, Any]]:
    daily = funnel.filter((pl.col("time_grain") == "day") & (pl.col("cohort_name") == "overall"))
    if daily.is_empty():
        return []
    latest = daily.get_column("period_start").max()
    rows = (
        daily.filter(pl.col("period_start") == latest)
        .select(
            "stage_order",
            "stage_name",
            "stage_count",
            "conversion_from_previous",
            "conversion_from_start",
            "drop_off_count",
        )
        .sort("stage_order")
        .to_dicts()
    )
    return cast(list[dict[str, Any]], rows)


def build_analytics_summary(loaded: LoadedAnalytics) -> dict[str, Any]:
    metrics = loaded.tables["metric_observations"]
    funnel = loaded.tables["funnel_observations"]
    quality = loaded.tables["data_quality_results"]
    contributions = loaded.tables["contribution_observations"]
    return {
        "manifest": loaded.manifest.model_dump(mode="json"),
        "table_rows": {name: frame.height for name, frame in loaded.tables.items()},
        "metric_names": sorted(metrics.get_column("metric_name").unique().to_list()),
        "cohort_names": sorted(metrics.get_column("cohort_name").unique().to_list()),
        "quality": quality_summary(quality),
        "contribution_analyses": sorted(
            contributions.get_column("analysis_name").unique().to_list()
        )
        if not contributions.is_empty()
        else [],
        "latest_overall_daily_metrics": _latest_overall_metrics(metrics),
        "latest_overall_daily_funnel": _latest_overall_funnel(funnel),
    }

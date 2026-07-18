"""Human- and machine-readable detector summaries."""

from __future__ import annotations

from typing import Any

import polars as pl

from paic.detection.types import LoadedDetection


def build_detection_summary(loaded: LoadedDetection) -> dict[str, Any]:
    observations = loaded.tables["detector_observations"]
    events = loaded.tables["anomaly_events"]
    quality = loaded.tables["detection_quality_results"]
    benchmark = loaded.tables["benchmark_summary"]
    latest = (
        events.sort("started_at", descending=True).head(20).to_dicts()
        if not events.is_empty()
        else []
    )
    return {
        "manifest": loaded.manifest.model_dump(mode="json"),
        "eligible_observations": observations.filter(pl.col("is_eligible")).height,
        "anomaly_observations": observations.filter(pl.col("is_anomaly")).height,
        "quality": {
            "passed": quality.filter(pl.col("status") == "pass").height,
            "failed": quality.filter(pl.col("status") == "fail").height,
            "warnings": quality.filter(pl.col("status") == "warn").height,
            "total": quality.height,
        },
        "benchmark": benchmark.to_dicts()[0] if not benchmark.is_empty() else None,
        "latest_events": latest,
    }

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from paic.analytics.io import load_analytics
from paic.analytics.registry import ANALYTIC_DIMENSIONS
from paic.detection.config import DetectionConfig
from paic.detection.engine import (
    CusumState,
    DetectionBuildError,
    SequentialState,
    _apply_benchmark_scenarios,
    _build_events,
    _score_series,
    _season_matches,
    _select_metric_observations,
    _update_cusum,
    _update_sequential,
    build_detection,
)
from paic.detection.schema import conform_detection_frame
from paic.detection.types import DetectionBuildResult


def test_smoke_build_is_valid_but_not_eligible(
    detection_smoke_result: DetectionBuildResult,
) -> None:
    observations = detection_smoke_result.tables["detector_observations"]
    quality = detection_smoke_result.tables["detection_quality_results"]

    assert observations.height == 2
    assert observations.filter(pl.col("is_eligible")).is_empty()
    assert observations.filter(pl.col("is_anomaly")).is_empty()
    assert detection_smoke_result.tables["anomaly_events"].is_empty()
    assert detection_smoke_result.tables["benchmark_ground_truth"].is_empty()
    assert quality.filter(pl.col("status") == "fail").is_empty()


def test_standard_benchmark_meets_declared_quality_gates(
    detection_standard_result: DetectionBuildResult,
) -> None:
    tables = detection_standard_result.tables
    summary = tables["benchmark_summary"].to_dicts()[0]
    results = tables["benchmark_results"]

    assert summary["scenario_count"] == 10
    assert summary["scenario_recall"] == 1.0
    assert summary["precision"] >= 0.80
    assert summary["false_positive_rate"] <= 0.005
    assert summary["mean_detection_delay_periods"] <= 1.5
    assert results.get_column("detected").all()
    assert results.get_column("direction_match").all()
    assert tables["detection_quality_results"].filter(pl.col("status") == "fail").is_empty()


def test_standard_scores_are_auditable_and_use_no_lookahead(
    detection_standard_result: DetectionBuildResult,
) -> None:
    observations = detection_standard_result.tables["detector_observations"]
    eligible = observations.filter(pl.col("is_eligible"))

    assert eligible.height > 1000
    assert eligible.get_column("p_value").is_not_null().all()
    assert eligible.get_column("q_value").is_not_null().all()
    assert eligible.filter(pl.col("q_value") < pl.col("p_value")).is_empty()
    assert observations.filter(
        pl.col("baseline_last_period").is_not_null()
        & (pl.col("baseline_last_period") >= pl.col("period_start"))
    ).is_empty()
    assert set(eligible.get_column("distribution_name").unique()) >= {
        "empirical-beta-binomial",
        "robust-log-student-t",
    }
    assert set(
        eligible.filter(pl.col("metric_name") == "completed_orders")
        .get_column("distribution_name")
        .unique()
    ) <= {
        "poisson",
        "negative-binomial",
    }
    assert observations.filter(pl.col("is_anomaly") & ~pl.col("is_eligible")).is_empty()


def test_standard_build_is_deterministic(
    analytics_standard_dir: Path,
    detection_standard_config: DetectionConfig,
    detection_standard_result: DetectionBuildResult,
) -> None:
    repeated = build_detection(analytics_standard_dir, detection_standard_config)
    for name, first in detection_standard_result.tables.items():
        assert repeated.tables[name].equals(first), name


def test_future_observation_cannot_change_prior_scores(
    analytics_standard_dir: Path,
    detection_standard_config: DetectionConfig,
) -> None:
    metrics = load_analytics(analytics_standard_dir).tables["metric_observations"]
    selected = _select_metric_observations(metrics, detection_standard_config)
    counts = selected.group_by("series_id").len().sort("len", descending=True)
    series_id = str(counts.row(0, named=True)["series_id"])
    series = selected.filter(pl.col("series_id") == series_id).sort("period_start")
    target = series.get_column("period_start")[-1]

    baseline = _score_series(selected, detection_standard_config).filter(
        (pl.col("series_id") == series_id) & (pl.col("period_start") < target)
    )
    mutated = selected.with_columns(
        pl.when((pl.col("series_id") == series_id) & (pl.col("period_start") == target))
        .then(pl.col("value") * 100.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    rescored = _score_series(mutated, detection_standard_config).filter(
        (pl.col("series_id") == series_id) & (pl.col("period_start") < target)
    )

    assert rescored.equals(baseline)


def test_seasonal_matching_uses_the_configured_cycle() -> None:
    current = datetime(2026, 1, 8, 12, tzinfo=UTC)
    assert _season_matches(current, current - timedelta(days=7), "hour", 24 * 7)
    assert not _season_matches(current, current - timedelta(days=6), "hour", 24 * 7)


def test_build_rejects_tampered_source_analytics_table(
    tmp_path: Path,
    analytics_smoke_dir: Path,
    detection_smoke_config: DetectionConfig,
) -> None:
    copied = tmp_path / "analytics"
    shutil.copytree(analytics_smoke_dir, copied)
    metrics_path = copied / "tables" / "metric_observations.parquet"
    metrics = pl.read_parquet(metrics_path).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.col("value") * 2.0)
        .otherwise(pl.col("value"))
        .alias("value")
    )
    metrics.write_parquet(metrics_path, compression="zstd", compression_level=6)

    with pytest.raises(DetectionBuildError, match="analytical artifact validation failed"):
        build_detection(copied, detection_smoke_config)


def test_selector_rejects_unavailable_cohort(
    analytics_smoke_dir: Path,
    detection_smoke_config: DetectionConfig,
) -> None:
    metrics = load_analytics(analytics_smoke_dir).tables["metric_observations"]
    raw = detection_smoke_config.model_dump(mode="json")
    raw["selectors"][0]["cohorts"] = ["not-present"]
    config = DetectionConfig.model_validate(raw)
    with pytest.raises(DetectionBuildError, match="unavailable cohorts"):
        _select_metric_observations(metrics, config)


def test_benchmark_requires_exact_non_overlapping_target_periods(
    analytics_standard_dir: Path,
    detection_standard_config: DetectionConfig,
) -> None:
    metrics = load_analytics(analytics_standard_dir).tables["metric_observations"]
    selected = _select_metric_observations(metrics, detection_standard_config)

    scenario = detection_standard_config.benchmark_scenarios[0].model_copy(
        update={"duration_periods": 200}
    )
    with pytest.raises(DetectionBuildError, match="expected 200 periods"):
        _apply_benchmark_scenarios(selected, [scenario])

    first = detection_standard_config.benchmark_scenarios[0]
    duplicate = first.model_copy(update={"scenario_id": "overlapping-copy"})
    with pytest.raises(DetectionBuildError, match="overlaps another scenario"):
        _apply_benchmark_scenarios(selected, [first, duplicate])


def test_build_rejects_analytics_with_quality_errors(
    tmp_path: Path,
    analytics_smoke_dir: Path,
    detection_smoke_config: DetectionConfig,
) -> None:
    copied = tmp_path / "analytics"
    shutil.copytree(analytics_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["quality_error_count"] = 1
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    (copied / "_SUCCESS").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
    )

    with pytest.raises(DetectionBuildError, match="analytical artifact has 1 quality errors"):
        build_detection(copied, detection_smoke_config)


def test_cusum_and_sequential_detectors_emit_only_on_threshold_crossing() -> None:
    period = datetime(2026, 1, 1, tzinfo=UTC)
    cusum = CusumState()
    scores = [
        _update_cusum(cusum, 3.0, period + timedelta(hours=index), drift=0.5, threshold=4.0)
        for index in range(3)
    ]
    assert [item[1] for item in scores] == [False, True, False]
    assert scores[1][2] == period

    sequential = SequentialState()
    sequence = [_update_sequential(sequential, 3.0, shift=1.5, threshold=4.0) for _ in range(3)]
    assert [item[1] for item in sequence] == [False, True, False]


def test_event_builder_merges_contiguous_points_and_splits_gaps(
    detection_standard_result: DetectionBuildResult,
) -> None:
    source = detection_standard_result.tables["detector_observations"].head(3)
    base = source.to_dicts()[0]
    rows: list[dict[str, object]] = []
    for offset in (0, 1, 3):
        row = dict(base)
        row["observation_id"] = f"OBS-{offset}"
        row["period_start"] = datetime(2026, 1, 1, offset, tzinfo=UTC)
        row["period_end"] = datetime(2026, 1, 1, offset + 1, tzinfo=UTC)
        row["is_eligible"] = True
        row["is_anomaly"] = True
        row["q_value"] = 0.01
        row["robust_z"] = 5.0
        row["relative_change"] = -0.5
        row["evidence_score"] = 0.8
        row["severity"] = "high"
        row["direction"] = "decrease"
        row["impact_direction"] = "degradation"
        row["change_detected"] = offset == 0
        row["estimated_change_start"] = row["period_start"]
        rows.append(row)
    observations = conform_detection_frame(
        "detector_observations", pl.from_dicts(rows, infer_schema_length=None)
    )
    events, changes = _build_events(observations)
    assert events.height == 2
    assert sorted(events.get_column("observation_count").to_list()) == [1, 2]
    assert changes.height == 1
    for dimension in ANALYTIC_DIMENSIONS:
        assert dimension in events.columns

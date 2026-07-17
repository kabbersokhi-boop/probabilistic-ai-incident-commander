from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from paic.detection.manifest import (
    DetectionColumnManifest,
    DetectionManifest,
    DetectionTableManifest,
)
from paic.detection.schema import conform_detection_frame, empty_detection_frame


def _table_payload() -> dict[str, object]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "name": "sample",
        "relative_path": "tables/sample.parquet",
        "row_count": 0,
        "byte_size": 0,
        "sha256": "0" * 64,
        "primary_key": ["id"],
        "columns": [
            {"name": "id", "dtype": "String", "nullable": False},
            {"name": "at", "dtype": "Datetime", "nullable": True},
        ],
        "minimum_timestamp": start,
        "maximum_timestamp": start + timedelta(hours=1),
    }


def test_table_manifest_rejects_unsafe_paths() -> None:
    for path in ("../escape.parquet", "/absolute.parquet", "tables\\bad.parquet"):
        raw = _table_payload()
        raw["relative_path"] = path
        with pytest.raises(ValueError, match="relative_path"):
            DetectionTableManifest.model_validate(raw)


def test_table_manifest_rejects_duplicate_or_unknown_metadata() -> None:
    raw = _table_payload()
    raw["columns"] = [raw["columns"][0], raw["columns"][0]]  # type: ignore[index]
    with pytest.raises(ValueError, match="column names must be unique"):
        DetectionTableManifest.model_validate(raw)

    raw = _table_payload()
    raw["primary_key"] = ["id", "id"]
    with pytest.raises(ValueError, match="primary-key columns must be unique"):
        DetectionTableManifest.model_validate(raw)

    raw = _table_payload()
    raw["primary_key"] = ["missing"]
    with pytest.raises(ValueError, match="unknown columns"):
        DetectionTableManifest.model_validate(raw)

    raw = _table_payload()
    raw["minimum_timestamp"], raw["maximum_timestamp"] = (
        raw["maximum_timestamp"],
        raw["minimum_timestamp"],
    )
    with pytest.raises(ValueError, match="must not be after"):
        DetectionTableManifest.model_validate(raw)


def test_detection_manifest_cross_field_invariants() -> None:
    # A compact manifest payload isolates model-level invariants from I/O tests.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    table = DetectionTableManifest.model_validate(_table_payload())
    payload = {
        "schema_version": "1.0",
        "detection_id": "test-detection",
        "generator_version": "0.4.0",
        "runtime": {
            "python_version": "3.11.0",
            "python_implementation": "CPython",
            "platform": "linux",
            "packages": {"scipy": "1.12.0"},
        },
        "source_analytics_id": "analytics",
        "source_analytics_manifest_sha256": "1" * 64,
        "source_analytics_config_sha256": "2" * 64,
        "detection_config_sha256": "3" * 64,
        "logical_start_at": start,
        "logical_end_at": start + timedelta(days=1),
        "selected_metric_count": 1,
        "selected_series_count": 1,
        "observation_count": 1,
        "anomaly_observation_count": 0,
        "anomaly_event_count": 0,
        "change_point_count": 0,
        "benchmark_scenario_count": 0,
        "quality_error_count": 0,
        "tables": [table.model_dump(mode="json")],
    }
    assert DetectionManifest.model_validate(payload).detection_id == "test-detection"

    invalid = dict(payload)
    invalid["logical_end_at"] = start
    with pytest.raises(ValueError, match="logical_end_at"):
        DetectionManifest.model_validate(invalid)

    invalid = dict(payload)
    invalid["tables"] = [table.model_dump(mode="json"), table.model_dump(mode="json")]
    with pytest.raises(ValueError, match="table names must be unique"):
        DetectionManifest.model_validate(invalid)

    second = table.model_copy(update={"name": "other"})
    invalid = dict(payload)
    invalid["tables"] = [table.model_dump(mode="json"), second.model_dump(mode="json")]
    with pytest.raises(ValueError, match="table paths must be unique"):
        DetectionManifest.model_validate(invalid)

    invalid = dict(payload)
    invalid["anomaly_observation_count"] = 2
    with pytest.raises(ValueError, match="cannot exceed observations"):
        DetectionManifest.model_validate(invalid)


def test_detection_schema_helpers() -> None:
    empty = empty_detection_frame("benchmark_summary")
    assert empty.is_empty()
    assert empty.columns[0] == "scope"

    with pytest.raises(ValueError, match="missing columns"):
        conform_detection_frame("benchmark_summary", pl.DataFrame({"scope": ["overall"]}))

    columns = {
        "scope": ["overall"],
        "scenario_count": [1],
        "scenarios_detected": [1],
        "observation_true_positives": [1],
        "observation_false_positives": [0],
        "observation_false_negatives": [0],
        "eligible_non_scenario_points": [10],
        "precision": [1.0],
        "scenario_recall": [1.0],
        "point_recall": [1.0],
        "false_positive_rate": [0.0],
        "mean_detection_delay_periods": [0.0],
        "median_detection_delay_periods": [0.0],
    }
    frame = conform_detection_frame("benchmark_summary", pl.DataFrame(columns))
    assert frame.schema["scenario_count"] == pl.Int64


def test_column_manifest_is_strict() -> None:
    column = DetectionColumnManifest(name="value", dtype="Float64", nullable=True)
    assert column.nullable is True
    with pytest.raises(ValueError):
        DetectionColumnManifest.model_validate(
            {"name": "value", "dtype": "Float64", "nullable": True, "extra": 1}
        )

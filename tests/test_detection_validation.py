from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import polars as pl

from paic.detection.io import export_detection, load_manifest
from paic.detection.types import DetectionBuildResult
from paic.detection.validation import validate_detection_directory
from paic.simulator.io import file_sha256


def _rewrite_manifest(root: Path, transform: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    path = root / "manifest.json"
    raw = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    transform(raw)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
    )
    return raw


def test_validation_detects_missing_or_invalid_config_and_table_set(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    missing_config = tmp_path / "missing-config"
    shutil.copytree(detection_smoke_dir, missing_config)
    (missing_config / "detection.config.resolved.json").unlink()
    report = validate_detection_directory(missing_config)
    assert "detection.config" in {item.code for item in report.issues}

    invalid_config = tmp_path / "invalid-config"
    shutil.copytree(detection_smoke_dir, invalid_config)
    config_path = invalid_config / "detection.config.resolved.json"
    config_path.write_text("{}\n", encoding="utf-8")
    _rewrite_manifest(
        invalid_config,
        lambda raw: raw.__setitem__("detection_config_sha256", file_sha256(config_path)),
    )
    report = validate_detection_directory(invalid_config)
    assert "detection.config" in {item.code for item in report.issues}

    mismatch = tmp_path / "id-mismatch"
    shutil.copytree(detection_smoke_dir, mismatch)
    config_path = mismatch / "detection.config.resolved.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["detection_id"] = "other-detection"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _rewrite_manifest(
        mismatch,
        lambda raw: raw.__setitem__("detection_config_sha256", file_sha256(config_path)),
    )
    report = validate_detection_directory(mismatch)
    assert "manifest.detection_id" in {item.code for item in report.issues}

    missing_table = tmp_path / "missing-table-manifest"
    shutil.copytree(detection_smoke_dir, missing_table)
    _rewrite_manifest(missing_table, lambda raw: cast(list[Any], raw["tables"]).pop())
    report = validate_detection_directory(missing_table)
    assert "manifest.table_set" in {item.code for item in report.issues}


def test_validation_detects_table_schema_primary_key_and_count_drift(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    copied = tmp_path / "schema-drift"
    shutil.copytree(detection_smoke_dir, copied)
    manifest = load_manifest(copied)
    table = next(item for item in manifest.tables if item.name == "detector_observations")
    path = copied / table.relative_path
    frame = pl.read_parquet(path)
    first = frame.head(1)
    changed = pl.concat([frame, first], how="vertical").with_columns(
        pl.col("sample_size").cast(pl.Float64)
    )
    columns = list(changed.columns)
    columns[0], columns[1] = columns[1], columns[0]
    changed = changed.select(columns)
    changed.write_parquet(path, compression="zstd", compression_level=6)

    def transform(raw: dict[str, Any]) -> None:
        raw["observation_count"] = changed.height
        tables = cast(list[dict[str, Any]], raw["tables"])
        item = next(value for value in tables if value["name"] == "detector_observations")
        item["row_count"] = changed.height
        item["byte_size"] = path.stat().st_size
        item["sha256"] = file_sha256(path)
        item["primary_key"] = []

    _rewrite_manifest(copied, transform)
    report = validate_detection_directory(copied)
    codes = {item.code for item in report.issues}
    assert {
        "manifest.columns",
        "manifest.dtypes",
        "manifest.primary_key",
        "table.primary_key",
    } <= codes


def test_validation_detects_manifest_summary_count_and_benchmark_drift(
    tmp_path: Path,
    detection_standard_result: DetectionBuildResult,
) -> None:
    original = tmp_path / "original"
    export_detection(detection_standard_result, original)
    copied = tmp_path / "counts"
    shutil.copytree(original, copied)

    def transform(raw: dict[str, Any]) -> None:
        raw["observation_count"] = int(cast(int, raw["observation_count"])) + 1
        raw["anomaly_observation_count"] = int(cast(int, raw["anomaly_observation_count"])) + 1
        raw["anomaly_event_count"] = int(cast(int, raw["anomaly_event_count"])) + 1
        raw["change_point_count"] = int(cast(int, raw["change_point_count"])) + 1
        raw["benchmark_scenario_count"] = int(cast(int, raw["benchmark_scenario_count"])) + 1
        raw["quality_error_count"] = int(cast(int, raw["quality_error_count"])) + 1
        raw["benchmark_precision"] = 0.0

    _rewrite_manifest(copied, transform)
    report = validate_detection_directory(copied)
    codes = {item.code for item in report.issues}
    assert {
        "manifest.observation_count",
        "manifest.anomaly_count",
        "manifest.event_count",
        "manifest.change_count",
        "manifest.benchmark_count",
        "manifest.quality_count",
        "manifest.benchmark_summary",
    } <= codes


def test_validation_reports_missing_source_analytics(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    report = validate_detection_directory(
        detection_smoke_dir, analytics_dir=tmp_path / "missing-analytics"
    )
    assert "source.analytics" in {item.code for item in report.issues}

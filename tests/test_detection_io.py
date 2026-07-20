from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from paic.detection.io import (
    DetectionIOError,
    export_detection,
    load_detection,
    load_manifest,
)
from paic.detection.types import DetectionBuildResult
from paic.detection.validation import (
    detection_report_to_json,
    validate_detection_directory,
)
from paic.simulator.io import file_sha256


def test_export_round_trip_manifest_and_source_lineage(
    tmp_path: Path,
    detection_smoke_result: DetectionBuildResult,
    analytics_smoke_dir: Path,
) -> None:
    output = tmp_path / "detection"
    manifest = export_detection(detection_smoke_result, output)
    loaded = load_detection(output)

    assert loaded.manifest == manifest
    assert len(manifest.tables) == 7
    assert manifest.observation_count == 2
    assert manifest.anomaly_observation_count == 0
    assert manifest.source_analytics_manifest_sha256 == file_sha256(
        analytics_smoke_dir / "manifest.json"
    )
    assert manifest.detection_config_sha256 == file_sha256(
        output / "detection.config.resolved.json"
    )
    assert (output / "_SUCCESS").read_text(encoding="utf-8").strip() == file_sha256(
        output / "manifest.json"
    )
    assert manifest.runtime.packages["scipy"]
    assert validate_detection_directory(output, analytics_dir=analytics_smoke_dir).valid


def test_exports_are_deterministic_in_same_runtime(
    tmp_path: Path,
    detection_smoke_result: DetectionBuildResult,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = export_detection(detection_smoke_result, first)
    second_manifest = export_detection(detection_smoke_result, second)

    assert first_manifest == second_manifest
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert (first / "detection.config.resolved.json").read_bytes() == (
        second / "detection.config.resolved.json"
    ).read_bytes()
    assert [item.sha256 for item in first_manifest.tables] == [
        item.sha256 for item in second_manifest.tables
    ]


def test_export_requires_explicit_overwrite_and_rejects_file_target(
    tmp_path: Path,
    detection_smoke_result: DetectionBuildResult,
) -> None:
    output = tmp_path / "detection"
    export_detection(detection_smoke_result, output)
    with pytest.raises(DetectionIOError, match="already exists"):
        export_detection(detection_smoke_result, output)
    export_detection(detection_smoke_result, output, overwrite=True)

    target = tmp_path / "target-file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(DetectionIOError, match="artifact target must be a directory"):
        export_detection(detection_smoke_result, target, overwrite=True)


def test_export_rejects_missing_required_table(
    tmp_path: Path,
    detection_smoke_result: DetectionBuildResult,
) -> None:
    tables = dict(detection_smoke_result.tables)
    tables.pop("anomaly_events")
    broken = DetectionBuildResult(
        config=detection_smoke_result.config,
        source_manifest=detection_smoke_result.source_manifest,
        source_manifest_sha256=detection_smoke_result.source_manifest_sha256,
        tables=tables,
    )
    with pytest.raises(DetectionIOError, match="missing detection table"):
        export_detection(broken, tmp_path / "detection")


def test_loading_rejects_missing_invalid_and_unsafe_manifest(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    with pytest.raises(DetectionIOError, match="cannot read detection manifest"):
        load_manifest(tmp_path / "missing")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(DetectionIOError, match="invalid detection manifest"):
        load_manifest(invalid)

    copied = tmp_path / "unsafe"
    shutil.copytree(detection_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"][0]["relative_path"] = "../outside.parquet"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DetectionIOError, match="inside the detection directory"):
        load_manifest(copied)


def test_validation_detects_marker_config_and_manifest_tampering(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    marker_copy = tmp_path / "marker"
    shutil.copytree(detection_smoke_dir, marker_copy)
    (marker_copy / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    report = validate_detection_directory(marker_copy)
    assert not report.valid
    assert "detection.success_marker" in {item.code for item in report.issues}

    config_copy = tmp_path / "config"
    shutil.copytree(detection_smoke_dir, config_copy)
    config_path = config_copy / "detection.config.resolved.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["alert_policy"]["fdr_alpha"] = 0.1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    report = validate_detection_directory(config_copy)
    assert not report.valid
    assert "detection.config_hash" in {item.code for item in report.issues}

    manifest_copy = tmp_path / "manifest"
    shutil.copytree(detection_smoke_dir, manifest_copy)
    manifest_path = manifest_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"][0]["row_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (manifest_copy / "_SUCCESS").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
    )
    report = validate_detection_directory(manifest_copy)
    assert not report.valid
    assert "manifest.row_count" in {item.code for item in report.issues}


def test_validation_reports_corrupt_and_readable_hash_drift(
    tmp_path: Path,
    detection_smoke_dir: Path,
) -> None:
    corrupted = tmp_path / "corrupted"
    shutil.copytree(detection_smoke_dir, corrupted)
    manifest = load_manifest(corrupted)
    table_path = corrupted / manifest.tables[0].relative_path
    table_path.write_bytes(b"not parquet")
    report = validate_detection_directory(corrupted)
    assert not report.valid
    assert report.issues[0].code == "detection.load"

    readable = tmp_path / "readable"
    shutil.copytree(detection_smoke_dir, readable)
    manifest = load_manifest(readable)
    table = next(item for item in manifest.tables if item.name == "detection_quality_results")
    path = readable / table.relative_path
    frame = pl.read_parquet(path).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit("tampered"))
        .otherwise(pl.col("details"))
        .alias("details")
    )
    frame.write_parquet(path, compression="zstd", compression_level=6)
    report = validate_detection_directory(readable)
    assert not report.valid
    assert "manifest.hash_mismatch" in {item.code for item in report.issues}


def test_validation_detects_source_analytics_lineage_mismatch(
    tmp_path: Path,
    detection_smoke_dir: Path,
    analytics_smoke_dir: Path,
) -> None:
    analytics_copy = tmp_path / "analytics"
    shutil.copytree(analytics_smoke_dir, analytics_copy)
    manifest_path = analytics_copy / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["analytics_id"] = "different-analytics"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    report = validate_detection_directory(detection_smoke_dir, analytics_dir=analytics_copy)
    assert not report.valid
    codes = {item.code for item in report.issues}
    assert "source.manifest_hash" in codes
    assert "source.analytics_id" in codes
    assert json.loads(detection_report_to_json(report))["valid"] is False

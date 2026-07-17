from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from paic.analytics.io import (
    AnalyticsIOError,
    export_analytics,
    load_analytics,
    load_manifest,
)
from paic.analytics.types import AnalyticsBuildResult
from paic.analytics.validation import validate_analytics_directory
from paic.simulator.io import file_sha256


def test_export_round_trip_hashes_and_success_marker(
    tmp_path: Path,
    analytics_smoke_result: AnalyticsBuildResult,
    smoke_dataset_dir: Path,
) -> None:
    output = tmp_path / "analytics"
    manifest = export_analytics(analytics_smoke_result, output)
    loaded = load_analytics(output)

    assert loaded.manifest == manifest
    assert set(loaded.tables) == {
        "metric_observations",
        "funnel_observations",
        "contribution_observations",
        "data_quality_results",
    }
    assert manifest.analytics_config_sha256 == file_sha256(
        output / "analytics.config.resolved.json"
    )
    assert manifest.metric_catalog_sha256 == file_sha256(output / "metric_catalog.json")
    assert (output / "_SUCCESS").read_text(encoding="utf-8").strip() == file_sha256(
        output / "manifest.json"
    )
    report = validate_analytics_directory(output, dataset_dir=smoke_dataset_dir)
    assert report.valid
    assert report.statistics["quality_errors"] == 0


def test_exports_are_deterministic_within_the_same_runtime(
    tmp_path: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = export_analytics(analytics_smoke_result, first)
    second_manifest = export_analytics(analytics_smoke_result, second)

    assert first_manifest == second_manifest
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert (first / "analytics.config.resolved.json").read_bytes() == (
        second / "analytics.config.resolved.json"
    ).read_bytes()
    assert (first / "metric_catalog.json").read_bytes() == (
        second / "metric_catalog.json"
    ).read_bytes()
    assert [item.sha256 for item in first_manifest.tables] == [
        item.sha256 for item in second_manifest.tables
    ]


def test_export_requires_explicit_overwrite_and_rejects_file_target(
    tmp_path: Path,
    analytics_smoke_result: AnalyticsBuildResult,
) -> None:
    output = tmp_path / "analytics"
    export_analytics(analytics_smoke_result, output)
    with pytest.raises(AnalyticsIOError, match="already exists"):
        export_analytics(analytics_smoke_result, output)
    export_analytics(analytics_smoke_result, output, overwrite=True)

    target = tmp_path / "file"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(AnalyticsIOError, match="output path is a file"):
        export_analytics(analytics_smoke_result, target, overwrite=True)


def test_loading_rejects_missing_invalid_and_unsafe_manifests(
    tmp_path: Path,
    analytics_smoke_dir: Path,
) -> None:
    with pytest.raises(AnalyticsIOError, match="cannot read analytics manifest"):
        load_manifest(tmp_path / "missing")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(AnalyticsIOError, match="invalid analytics manifest"):
        load_manifest(invalid)

    copied = tmp_path / "copied"
    shutil.copytree(analytics_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"][0]["relative_path"] = "../outside.parquet"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AnalyticsIOError, match="inside the analytics directory"):
        load_manifest(copied)


def test_validation_detects_marker_config_catalog_and_manifest_tampering(
    tmp_path: Path,
    analytics_smoke_dir: Path,
) -> None:
    marker_copy = tmp_path / "marker"
    shutil.copytree(analytics_smoke_dir, marker_copy)
    (marker_copy / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    marker_report = validate_analytics_directory(marker_copy)
    assert not marker_report.valid
    assert {item.code for item in marker_report.issues} >= {"analytics.success_marker"}

    config_copy = tmp_path / "config"
    shutil.copytree(analytics_smoke_dir, config_copy)
    config_path = config_copy / "analytics.config.resolved.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["minimum_denominator"] += 1
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_report = validate_analytics_directory(config_copy)
    assert not config_report.valid
    assert {item.code for item in config_report.issues} >= {"analytics.config_hash"}

    catalog_copy = tmp_path / "catalog"
    shutil.copytree(analytics_smoke_dir, catalog_copy)
    catalog_path = catalog_copy / "metric_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog[0]["display_name"] = "tampered"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    catalog_report = validate_analytics_directory(catalog_copy)
    assert not catalog_report.valid
    codes = {item.code for item in catalog_report.issues}
    assert "analytics.metric_catalog" in codes
    assert "analytics.metric_catalog_hash" in codes

    manifest_copy = tmp_path / "manifest"
    shutil.copytree(analytics_smoke_dir, manifest_copy)
    manifest_path = manifest_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"][0]["row_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (manifest_copy / "_SUCCESS").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n",
        encoding="utf-8",
    )
    manifest_report = validate_analytics_directory(manifest_copy)
    assert not manifest_report.valid
    assert "analytics.row_count" in {item.code for item in manifest_report.issues}


def test_validation_reports_corrupted_table_without_crashing(
    tmp_path: Path,
    analytics_smoke_dir: Path,
) -> None:
    copied = tmp_path / "corrupted"
    shutil.copytree(analytics_smoke_dir, copied)
    manifest = load_manifest(copied)
    table_path = copied / manifest.tables[0].relative_path
    table_path.write_bytes(b"not parquet")
    report = validate_analytics_directory(copied)
    assert not report.valid
    assert report.issues[0].code == "analytics.load"


def test_validation_detects_readable_table_hash_drift(
    tmp_path: Path,
    analytics_smoke_dir: Path,
) -> None:
    copied = tmp_path / "hash-drift"
    shutil.copytree(analytics_smoke_dir, copied)
    manifest = load_manifest(copied)
    table = next(item for item in manifest.tables if item.name == "data_quality_results")
    table_path = copied / table.relative_path
    frame = pl.read_parquet(table_path).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit("tampered"))
        .otherwise(pl.col("details"))
        .alias("details")
    )
    frame.write_parquet(table_path, compression="zstd", compression_level=6)

    report = validate_analytics_directory(copied)

    assert not report.valid
    assert {item.code for item in report.issues} >= {"analytics.hash"}

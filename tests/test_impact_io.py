from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from paic.impact.io import ImpactIOError, export_impact, load_impact, load_manifest
from paic.impact.types import ImpactBuildResult
from paic.impact.validation import validate_impact_directory
from paic.simulator.io import file_sha256


def test_export_round_trip_is_deterministic(
    tmp_path: Path,
    impact_smoke_result: ImpactBuildResult,
    impact_smoke_dataset_dir: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = export_impact(impact_smoke_result, first)
    second_manifest = export_impact(impact_smoke_result, second)
    assert first_manifest == second_manifest
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert [item.sha256 for item in first_manifest.tables] == [
        item.sha256 for item in second_manifest.tables
    ]
    assert load_impact(first).manifest == first_manifest
    assert validate_impact_directory(first, dataset_dir=impact_smoke_dataset_dir).valid


def test_export_requires_overwrite(tmp_path: Path, impact_smoke_result: ImpactBuildResult) -> None:
    output = tmp_path / "impact"
    export_impact(impact_smoke_result, output)
    with pytest.raises(ImpactIOError, match="already exists"):
        export_impact(impact_smoke_result, output)
    export_impact(impact_smoke_result, output, overwrite=True)


def test_validation_detects_hash_and_financial_tampering(
    tmp_path: Path, impact_smoke_dir: Path
) -> None:
    copied = tmp_path / "copied"
    shutil.copytree(impact_smoke_dir, copied)
    manifest = load_manifest(copied)
    table = next(item for item in manifest.tables if item.name == "financial_impact")
    path = copied / table.relative_path
    frame = pl.read_parquet(path).with_columns(
        (pl.col("total_financial_impact") + 1.0).alias("total_financial_impact")
    )
    frame.write_parquet(path, compression="zstd", compression_level=6)
    report = validate_impact_directory(copied)
    codes = {item.code for item in report.issues}
    assert "impact.hash" in codes
    assert "impact.financial" in codes


def test_loading_rejects_unsafe_manifest_path(tmp_path: Path, impact_smoke_dir: Path) -> None:
    copied = tmp_path / "unsafe"
    shutil.copytree(impact_smoke_dir, copied)
    path = copied / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["tables"][0]["relative_path"] = "../outside.parquet"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ImpactIOError, match="inside the impact directory"):
        load_manifest(copied)


def test_source_binding_detects_wrong_dataset(
    impact_smoke_dir: Path, smoke_dataset_dir: Path
) -> None:
    report = validate_impact_directory(impact_smoke_dir, dataset_dir=smoke_dataset_dir)
    assert "source.dataset" in {item.code for item in report.issues}


def test_validation_recomputes_semantics_after_rehashed_propensity_tampering(
    tmp_path: Path,
    impact_smoke_dir: Path,
    impact_smoke_dataset_dir: Path,
) -> None:
    copied = tmp_path / "semantic-tamper"
    shutil.copytree(impact_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    table = next(item for item in raw["tables"] if item["name"] == "propensity_scores")
    path = copied / table["relative_path"]
    frame = pl.read_parquet(path).with_columns(
        (pl.col("stabilized_weight") * 1.01).alias("stabilized_weight")
    )
    frame.write_parquet(path, compression="zstd", compression_level=6)
    table["sha256"] = file_sha256(path)
    table["byte_size"] = path.stat().st_size
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    (copied / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")

    report = validate_impact_directory(copied, dataset_dir=impact_smoke_dataset_dir)
    assert "impact.recompute" in {item.code for item in report.issues}

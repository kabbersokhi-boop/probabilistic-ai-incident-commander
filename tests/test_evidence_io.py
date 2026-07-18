from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, cast

import polars as pl

from paic.evidence.io import export_evidence, load_evidence, load_manifest
from paic.evidence.types import EvidenceBuildResult
from paic.evidence.validation import validate_evidence_directory
from paic.simulator.io import file_sha256


def _rewrite_manifest(root: Path, transform: Any) -> None:
    path = root / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    transform(raw)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
    )


def test_export_round_trip_and_determinism(
    tmp_path: Path, evidence_smoke_result: EvidenceBuildResult
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = export_evidence(evidence_smoke_result, first)
    second_manifest = export_evidence(evidence_smoke_result, second)
    assert first_manifest == second_manifest
    assert (
        load_evidence(first)
        .tables["evidence_records"]
        .equals(evidence_smoke_result.tables["evidence_records"])
    )
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert [item.sha256 for item in first_manifest.tables] == [
        item.sha256 for item in second_manifest.tables
    ]


def test_validation_rejects_semantic_tampering_with_refreshed_hashes(
    tmp_path: Path,
    evidence_smoke_dir: Path,
    impact_smoke_dataset_dir: Path,
) -> None:
    copied = tmp_path / "tampered"
    shutil.copytree(evidence_smoke_dir, copied)
    manifest = load_manifest(copied)
    table = next(item for item in manifest.tables if item.name == "config_changes")
    path = copied / table.relative_path
    frame = pl.read_parquet(path).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit("tampered"))
        .otherwise(pl.col("description"))
        .alias("description")
    )
    frame.write_parquet(path, compression="zstd", compression_level=6)

    def transform(raw: dict[str, Any]) -> None:
        tables = cast(list[dict[str, Any]], raw["tables"])
        item = next(value for value in tables if value["name"] == "config_changes")
        item["byte_size"] = path.stat().st_size
        item["sha256"] = file_sha256(path)

    _rewrite_manifest(copied, transform)
    report = validate_evidence_directory(copied, dataset_dir=impact_smoke_dataset_dir)
    assert not report.valid
    assert "evidence.recompute" in {item.code for item in report.issues}


def test_validation_detects_unsafe_manifest_path(tmp_path: Path, evidence_smoke_dir: Path) -> None:
    copied = tmp_path / "unsafe"
    shutil.copytree(evidence_smoke_dir, copied)
    path = copied / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["tables"][0]["relative_path"] = "../outside.parquet"
    path.write_text(json.dumps(raw), encoding="utf-8")
    report = validate_evidence_directory(copied)
    assert not report.valid
    assert report.issues[0].code == "evidence.load"


def test_validation_rejects_refreshed_detection_manifest_without_analytics(
    tmp_path: Path, evidence_smoke_dir: Path
) -> None:
    copied = tmp_path / "detection-without-analytics"
    shutil.copytree(evidence_smoke_dir, copied)

    def transform(raw: dict[str, Any]) -> None:
        raw["source_detection_manifest_sha256"] = "0" * 64

    _rewrite_manifest(copied, transform)
    report = validate_evidence_directory(copied)
    assert not report.valid
    assert "source.detection_binding" in {issue.code for issue in report.issues}


def test_validation_rejects_refreshed_canonical_payload_drift(
    tmp_path: Path, evidence_smoke_dir: Path
) -> None:
    copied = tmp_path / "payload-drift"
    shutil.copytree(evidence_smoke_dir, copied)
    manifest = load_manifest(copied)
    table = next(item for item in manifest.tables if item.name == "evidence_records")
    path = copied / table.relative_path
    frame = pl.read_parquet(path).with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit('{"forged":true}'))
        .otherwise(pl.col("payload_json"))
        .alias("payload_json")
    )
    frame.write_parquet(path, compression="zstd", compression_level=6)

    def transform(raw: dict[str, Any]) -> None:
        tables = cast(list[dict[str, Any]], raw["tables"])
        item = next(value for value in tables if value["name"] == "evidence_records")
        item["byte_size"] = path.stat().st_size
        item["sha256"] = file_sha256(path)

    _rewrite_manifest(copied, transform)
    codes = {item.code for item in validate_evidence_directory(copied).issues}
    assert {"evidence.content", "evidence.id"}.issubset(codes)


def test_export_overwrite_and_load_failures(
    tmp_path: Path, evidence_smoke_result: EvidenceBuildResult
) -> None:
    from paic.evidence.io import EvidenceIOError

    output = tmp_path / "artifact"
    export_evidence(evidence_smoke_result, output)
    try:
        export_evidence(evidence_smoke_result, output)
    except EvidenceIOError as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("overwrite should be explicit")
    export_evidence(evidence_smoke_result, output, overwrite=True)

    file_target = tmp_path / "file"
    file_target.write_text("x", encoding="utf-8")
    try:
        export_evidence(evidence_smoke_result, file_target, overwrite=True)
    except EvidenceIOError as exc:
        assert "output path is a file" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("file targets must be rejected")

    try:
        load_manifest(tmp_path / "missing")
    except EvidenceIOError as exc:
        assert "cannot read" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing manifests must fail")


def test_validation_detects_marker_config_and_source_mismatch(
    tmp_path: Path,
    evidence_smoke_dir: Path,
    impact_smoke_dataset_dir: Path,
) -> None:
    marker_copy = tmp_path / "marker"
    shutil.copytree(evidence_smoke_dir, marker_copy)
    (marker_copy / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    assert "evidence.success_marker" in {
        item.code for item in validate_evidence_directory(marker_copy).issues
    }

    config_copy = tmp_path / "config"
    shutil.copytree(evidence_smoke_dir, config_copy)
    path = config_copy / "evidence.config.resolved.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["evidence_id"] = "tampered-evidence"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert "evidence.config_hash" in {
        item.code for item in validate_evidence_directory(config_copy).issues
    }

    source_copy = tmp_path / "source"
    shutil.copytree(impact_smoke_dataset_dir, source_copy)
    manifest_path = source_copy / "manifest.json"
    source_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_raw["simulation_id"] = "different-source"
    manifest_path.write_text(json.dumps(source_raw), encoding="utf-8")
    report = validate_evidence_directory(evidence_smoke_dir, dataset_dir=source_copy)
    assert "source.dataset" in {item.code for item in report.issues}

"""Integrity and semantic validation for exported detection artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import polars as pl

from paic.analytics.io import load_manifest as load_analytics_manifest
from paic.detection.config import DetectionConfig
from paic.detection.engine import detection_quality_error_count
from paic.detection.io import DetectionIOError, load_detection
from paic.detection.schema import DETECTION_TABLE_ORDER, DETECTION_TABLE_SPECS
from paic.simulator.io import file_sha256


@dataclass(frozen=True)
class DetectionValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class DetectionValidationReport:
    valid: bool
    issues: list[DetectionValidationIssue]
    summary: dict[str, int]


def _issue(issues: list[DetectionValidationIssue], code: str, message: str) -> None:
    issues.append(DetectionValidationIssue(code=code, message=message))


def validate_detection_directory(
    detection_dir: str | Path,
    *,
    analytics_dir: str | Path | None = None,
) -> DetectionValidationReport:
    root = Path(detection_dir)
    issues: list[DetectionValidationIssue] = []
    try:
        loaded = load_detection(root)
    except DetectionIOError as exc:
        _issue(issues, "detection.load", str(exc))
        return DetectionValidationReport(valid=False, issues=issues, summary={})
    manifest = loaded.manifest

    manifest_path = root / "manifest.json"
    marker = root / "_SUCCESS"
    if not marker.is_file():
        _issue(issues, "detection.success_marker", "missing _SUCCESS marker")
    elif marker.read_text(encoding="utf-8").strip() != file_sha256(manifest_path):
        _issue(issues, "detection.success_marker", "_SUCCESS does not match manifest hash")

    config_path = root / "detection.config.resolved.json"
    config: DetectionConfig | None = None
    if not config_path.is_file():
        _issue(issues, "detection.config", "missing resolved detection configuration")
    else:
        if file_sha256(config_path) != manifest.detection_config_sha256:
            _issue(issues, "detection.config_hash", "resolved config hash differs from manifest")
        try:
            config = DetectionConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            _issue(issues, "detection.config", f"invalid resolved detection configuration: {exc}")
        if config is not None and config.detection_id != manifest.detection_id:
            _issue(issues, "manifest.detection_id", "manifest and config detection IDs differ")

    expected_names = list(DETECTION_TABLE_ORDER)
    actual_names = [item.name for item in manifest.tables]
    if actual_names != expected_names:
        _issue(
            issues,
            "manifest.table_set",
            f"expected tables {expected_names}, found {actual_names}",
        )

    for table_manifest in manifest.tables:
        frame = loaded.tables.get(table_manifest.name)
        if frame is None:
            _issue(issues, "manifest.table_missing", f"missing loaded table {table_manifest.name}")
            continue
        spec = DETECTION_TABLE_SPECS.get(table_manifest.name)
        if spec is None:
            _issue(issues, "manifest.table_unknown", f"unknown table {table_manifest.name}")
            continue
        path = root / table_manifest.relative_path
        if path.stat().st_size != table_manifest.byte_size:
            _issue(issues, "manifest.byte_size", f"byte size mismatch for {table_manifest.name}")
        if file_sha256(path) != table_manifest.sha256:
            _issue(issues, "manifest.hash_mismatch", f"hash mismatch for {table_manifest.name}")
        if frame.height != table_manifest.row_count:
            _issue(issues, "manifest.row_count", f"row count mismatch for {table_manifest.name}")
        expected_columns = [name for name, _ in spec.columns]
        if frame.columns != expected_columns:
            _issue(issues, "manifest.columns", f"column order mismatch for {table_manifest.name}")
        expected_dtypes = [str(dtype) for _, dtype in spec.columns]
        actual_dtypes = [str(dtype) for dtype in frame.dtypes]
        if actual_dtypes != expected_dtypes:
            _issue(issues, "manifest.dtypes", f"dtype mismatch for {table_manifest.name}")
        if table_manifest.primary_key != list(spec.primary_key):
            _issue(
                issues, "manifest.primary_key", f"primary key mismatch for {table_manifest.name}"
            )
        if spec.primary_key and not frame.is_empty():
            duplicate_count = int(frame.select(spec.primary_key).is_duplicated().sum())
            if duplicate_count:
                _issue(
                    issues,
                    "table.primary_key",
                    f"{table_manifest.name} has {duplicate_count} duplicate primary keys",
                )

    observations = loaded.tables.get("detector_observations", pl.DataFrame())
    events = loaded.tables.get("anomaly_events", pl.DataFrame())
    changes = loaded.tables.get("change_point_events", pl.DataFrame())
    truth = loaded.tables.get("benchmark_ground_truth", pl.DataFrame())
    quality = loaded.tables.get("detection_quality_results", pl.DataFrame())
    if observations.height != manifest.observation_count:
        _issue(issues, "manifest.observation_count", "observation count differs from manifest")
    anomaly_count = (
        observations.filter(pl.col("is_anomaly")).height if not observations.is_empty() else 0
    )
    if anomaly_count != manifest.anomaly_observation_count:
        _issue(issues, "manifest.anomaly_count", "anomaly count differs from manifest")
    if events.height != manifest.anomaly_event_count:
        _issue(issues, "manifest.event_count", "event count differs from manifest")
    if changes.height != manifest.change_point_count:
        _issue(issues, "manifest.change_count", "change-point count differs from manifest")
    if truth.height != manifest.benchmark_scenario_count:
        _issue(issues, "manifest.benchmark_count", "benchmark scenario count differs from manifest")
    quality_errors = detection_quality_error_count(quality) if not quality.is_empty() else 1
    if quality_errors != manifest.quality_error_count:
        _issue(issues, "manifest.quality_count", "quality error count differs from manifest")
    if quality_errors:
        _issue(issues, "detection.quality", f"artifact contains {quality_errors} quality errors")

    benchmark = loaded.tables.get("benchmark_summary", pl.DataFrame())
    if benchmark.is_empty():
        for field_name in (
            "benchmark_precision",
            "benchmark_scenario_recall",
            "benchmark_false_positive_rate",
            "benchmark_mean_delay_periods",
        ):
            if getattr(manifest, field_name) is not None:
                _issue(issues, "manifest.benchmark_summary", f"{field_name} should be null")
    else:
        row = benchmark.to_dicts()[0]
        pairs = {
            "benchmark_precision": row["precision"],
            "benchmark_scenario_recall": row["scenario_recall"],
            "benchmark_false_positive_rate": row["false_positive_rate"],
            "benchmark_mean_delay_periods": row["mean_detection_delay_periods"],
        }
        for field_name, expected in pairs.items():
            actual = getattr(manifest, field_name)
            if actual is None or expected is None or abs(float(actual) - float(expected)) > 1e-12:
                _issue(issues, "manifest.benchmark_summary", f"{field_name} differs from table")

    if analytics_dir is not None:
        analytics_root = Path(analytics_dir)
        try:
            source_manifest = load_analytics_manifest(analytics_root)
        except Exception as exc:
            _issue(issues, "source.analytics", f"cannot load source analytics: {exc}")
        else:
            if (
                file_sha256(analytics_root / "manifest.json")
                != manifest.source_analytics_manifest_sha256
            ):
                _issue(issues, "source.manifest_hash", "source analytics manifest hash differs")
            if source_manifest.analytics_id != manifest.source_analytics_id:
                _issue(issues, "source.analytics_id", "source analytics ID differs")
            if source_manifest.analytics_config_sha256 != manifest.source_analytics_config_sha256:
                _issue(issues, "source.config_hash", "source analytics config hash differs")
            if (
                source_manifest.logical_start_at != manifest.logical_start_at
                or source_manifest.logical_end_at != manifest.logical_end_at
            ):
                _issue(issues, "source.logical_window", "source and detection windows differ")

    summary = {
        "tables": len(manifest.tables),
        "observations": observations.height,
        "anomalies": anomaly_count,
        "events": events.height,
        "benchmarks": truth.height,
        "quality_errors": quality_errors,
    }
    return DetectionValidationReport(valid=not issues, issues=issues, summary=summary)


def detection_report_to_json(report: DetectionValidationReport) -> str:
    payload: dict[str, Any] = {
        "valid": report.valid,
        "issues": [asdict(item) for item in report.issues],
        "summary": report.summary,
    }
    return json.dumps(payload, indent=2, sort_keys=True)

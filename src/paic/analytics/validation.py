"""On-disk validation for deterministic analytical artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from paic.analytics.config import AnalyticsConfig
from paic.analytics.io import AnalyticsIOError, load_analytics, metric_catalog_json
from paic.analytics.quality import quality_error_count
from paic.analytics.schema import ANALYTICS_TABLE_ORDER, ANALYTICS_TABLE_SPECS
from paic.simulator.io import file_sha256
from paic.simulator.io import load_manifest as load_source_manifest
from paic.simulator.validation import validate_dataset_directory


@dataclass(frozen=True)
class AnalyticsValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    table: str | None
    message: str


@dataclass(frozen=True)
class AnalyticsValidationReport:
    issues: tuple[AnalyticsValidationIssue, ...]
    statistics: dict[str, float | int]

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [asdict(item) for item in self.issues],
            "statistics": self.statistics,
        }


def _issue(
    issues: list[AnalyticsValidationIssue],
    code: str,
    message: str,
    *,
    table: str | None = None,
    severity: Literal["error", "warning"] = "error",
) -> None:
    issues.append(AnalyticsValidationIssue(severity, code, table, message))


def _timestamp_bounds(
    frame: Any, columns: tuple[str, ...]
) -> tuple[datetime | None, datetime | None]:
    values: list[datetime] = []
    for column in columns:
        series = frame.get_column(column).drop_nulls()
        if series.is_empty():
            continue
        for value in (series.min(), series.max()):
            if isinstance(value, datetime):
                values.append(value)
    return (min(values), max(values)) if values else (None, None)


def validate_analytics_directory(
    analytics_dir: str | Path,
    *,
    dataset_dir: str | Path | None = None,
) -> AnalyticsValidationReport:
    root = Path(analytics_dir)
    issues: list[AnalyticsValidationIssue] = []
    statistics: dict[str, float | int] = {}
    try:
        loaded = load_analytics(root)
    except AnalyticsIOError as exc:
        return AnalyticsValidationReport(
            (AnalyticsValidationIssue("error", "analytics.load", None, str(exc)),), {}
        )
    manifest = loaded.manifest
    tables = loaded.tables

    manifest_path = root / "manifest.json"
    marker_path = root / "_SUCCESS"
    try:
        expected_marker = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        marker = marker_path.read_text(encoding="utf-8").strip()
        if marker != expected_marker:
            _issue(issues, "analytics.success_marker", "success marker does not match manifest")
    except OSError as exc:
        _issue(issues, "analytics.success_marker", f"cannot read success marker: {exc}")

    config_path = root / "analytics.config.resolved.json"
    try:
        config = AnalyticsConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        if file_sha256(config_path) != manifest.analytics_config_sha256:
            _issue(issues, "analytics.config_hash", "resolved analytics config hash mismatch")
        if config.analytics_id != manifest.analytics_id:
            _issue(issues, "analytics.id", "analytics_id differs between config and manifest")
        if len(config.metric_names) != manifest.metric_count:
            _issue(issues, "analytics.metric_count", "enabled metric count differs from manifest")
        if len(config.cohorts) != manifest.cohort_count:
            _issue(issues, "analytics.cohort_count", "cohort count differs from manifest")
        if len(config.contributions) != manifest.contribution_analysis_count:
            _issue(
                issues,
                "analytics.contribution_count",
                "contribution analysis count differs from manifest",
            )
    except Exception as exc:
        _issue(issues, "analytics.config", f"cannot validate analytics config: {exc}")

    catalog_path = root / "metric_catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog != json.loads(metric_catalog_json()):
            _issue(issues, "analytics.metric_catalog", "metric catalog differs from code registry")
        if file_sha256(catalog_path) != manifest.metric_catalog_sha256:
            _issue(issues, "analytics.metric_catalog_hash", "metric catalog hash mismatch")
    except Exception as exc:
        _issue(issues, "analytics.metric_catalog", f"cannot validate metric catalog: {exc}")

    manifest_tables = {item.name: item for item in manifest.tables}
    expected_names = set(ANALYTICS_TABLE_ORDER)
    actual_names = set(manifest_tables)
    for missing in sorted(expected_names.difference(actual_names)):
        _issue(issues, "analytics.table_missing", "required analytics table missing", table=missing)
    for extra in sorted(actual_names.difference(expected_names)):
        _issue(issues, "analytics.table_unregistered", "unregistered analytics table", table=extra)

    for table_name in ANALYTICS_TABLE_ORDER:
        item = manifest_tables.get(table_name)
        frame = tables.get(table_name)
        if item is None or frame is None:
            continue
        path = root / item.relative_path
        try:
            if path.stat().st_size != item.byte_size:
                _issue(
                    issues,
                    "analytics.byte_size",
                    "table byte size differs from manifest",
                    table=table_name,
                )
            if file_sha256(path) != item.sha256:
                _issue(
                    issues,
                    "analytics.hash",
                    "table SHA-256 differs from manifest",
                    table=table_name,
                )
        except OSError as exc:
            _issue(issues, "analytics.file", f"cannot inspect table file: {exc}", table=table_name)
            continue

        if frame.height != item.row_count:
            _issue(
                issues,
                "analytics.row_count",
                "table row count differs from manifest",
                table=table_name,
            )
        spec = ANALYTICS_TABLE_SPECS[table_name]
        expected_columns = [name for name, _ in spec.columns]
        if frame.columns != expected_columns:
            _issue(
                issues,
                "analytics.columns",
                "table columns differ from canonical schema",
                table=table_name,
            )
        for column, dtype in frame.schema.items():
            if spec.schema.get(column) != dtype:
                _issue(
                    issues,
                    "analytics.dtype",
                    f"column {column} has {dtype}, expected {spec.schema.get(column)}",
                    table=table_name,
                )
        if item.primary_key != list(spec.primary_key):
            _issue(
                issues,
                "analytics.primary_key_metadata",
                "manifest primary key differs from canonical schema",
                table=table_name,
            )
        duplicates = int(frame.select(list(spec.primary_key)).is_duplicated().sum())
        if duplicates:
            _issue(
                issues,
                "analytics.primary_key",
                f"table has {duplicates} duplicate primary-key rows",
                table=table_name,
            )

        manifest_columns = [(column.name, column.dtype) for column in item.columns]
        actual_columns = [(column, str(dtype)) for column, dtype in frame.schema.items()]
        if manifest_columns != actual_columns:
            _issue(
                issues,
                "analytics.column_metadata",
                "manifest column metadata differs from table",
                table=table_name,
            )
        minimum, maximum = _timestamp_bounds(frame, spec.timestamp_columns)
        if minimum != item.minimum_timestamp or maximum != item.maximum_timestamp:
            _issue(
                issues,
                "analytics.timestamp_bounds",
                "manifest timestamp bounds differ from table",
                table=table_name,
            )
        statistics[f"rows.{table_name}"] = frame.height

    quality = tables.get("data_quality_results")
    if quality is not None:
        errors = quality_error_count(quality)
        statistics["quality_errors"] = errors
        if errors != manifest.quality_error_count:
            _issue(issues, "analytics.quality_count", "quality error count differs from manifest")
        if errors:
            _issue(issues, "analytics.quality", f"artifact contains {errors} failed error checks")

    if dataset_dir is not None:
        source_root = Path(dataset_dir)
        source_report = validate_dataset_directory(source_root)
        if not source_report.valid:
            _issue(issues, "source.invalid", "source dataset validation failed")
        try:
            source_manifest = load_source_manifest(source_root)
            if source_manifest.simulation_id != manifest.source_simulation_id:
                _issue(issues, "source.simulation_id", "source simulation ID differs")
            if source_manifest.config_sha256 != manifest.source_config_sha256:
                _issue(issues, "source.config_hash", "source config hash differs")
            if file_sha256(source_root / "manifest.json") != manifest.source_manifest_sha256:
                _issue(issues, "source.manifest_hash", "source manifest hash differs")
        except Exception as exc:
            _issue(issues, "source.manifest", f"cannot validate source manifest: {exc}")

    return AnalyticsValidationReport(tuple(issues), statistics)


def analytics_report_to_json(report: AnalyticsValidationReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)

"""Read and write self-validating deterministic analytical artifacts."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

from paic import __version__
from paic.analytics.config import AnalyticsConfig
from paic.analytics.manifest import (
    AnalyticsColumnManifest,
    AnalyticsManifest,
    AnalyticsRuntimeManifest,
    AnalyticsTableManifest,
)
from paic.analytics.quality import quality_error_count
from paic.analytics.registry import metric_catalog
from paic.analytics.schema import ANALYTICS_TABLE_ORDER, ANALYTICS_TABLE_SPECS
from paic.analytics.types import AnalyticsBuildResult, AnalyticsFrameMap, LoadedAnalytics
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.simulator.io import file_sha256


class AnalyticsIOError(RuntimeError):
    """Raised when analytical artifacts cannot be safely read or written."""


def resolved_analytics_config_json(config: AnalyticsConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def metric_catalog_json() -> str:
    return json.dumps(metric_catalog(), indent=2, sort_keys=True) + "\n"


def analytics_config_hash(config: AnalyticsConfig) -> str:
    return hashlib.sha256(resolved_analytics_config_json(config).encode()).hexdigest()


def analytics_runtime_manifest() -> AnalyticsRuntimeManifest:
    packages: dict[str, str] = {}
    for distribution in ("numpy", "polars", "pyarrow", "pydantic", "PyYAML"):
        try:
            packages[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover - required dependencies are installed
            packages[distribution] = "unknown"
    return AnalyticsRuntimeManifest(
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        platform=sys.platform,
        packages=packages,
    )


def _timestamp_bounds(
    frame: pl.DataFrame, columns: tuple[str, ...]
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


def export_analytics(
    result: AnalyticsBuildResult,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> AnalyticsManifest:
    """Publish complete analytics without deleting the previous valid generation first."""

    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staging:
            manifest = _export_analytics_to_root(result, staging)
            publisher.commit()
            return manifest
    except ArtifactPublicationError as exc:
        raise AnalyticsIOError(str(exc)) from exc


def _export_analytics_to_root(result: AnalyticsBuildResult, root: Path) -> AnalyticsManifest:
    table_dir = root / "tables"
    table_dir.mkdir(parents=True, exist_ok=False)

    config_path = root / "analytics.config.resolved.json"
    config_path.write_text(resolved_analytics_config_json(result.config), encoding="utf-8")
    catalog_path = root / "metric_catalog.json"
    catalog_path.write_text(metric_catalog_json(), encoding="utf-8")

    table_manifests: list[AnalyticsTableManifest] = []
    for table_name in ANALYTICS_TABLE_ORDER:
        if table_name not in result.tables:
            raise AnalyticsIOError(f"missing analytics table: {table_name}")
        frame = result.tables[table_name]
        spec = ANALYTICS_TABLE_SPECS[table_name]
        relative_path = Path("tables") / f"{table_name}.parquet"
        path = root / relative_path
        kwargs: dict[str, Any] = {
            "compression": result.config.output.compression,
            "statistics": result.config.output.include_statistics,
            "row_group_size": result.config.output.row_group_size,
        }
        if result.config.output.compression_level is not None:
            kwargs["compression_level"] = result.config.output.compression_level
        frame.write_parquet(path, **kwargs)
        minimum, maximum = _timestamp_bounds(frame, spec.timestamp_columns)
        table_manifests.append(
            AnalyticsTableManifest(
                name=table_name,
                relative_path=relative_path.as_posix(),
                row_count=frame.height,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                primary_key=list(spec.primary_key),
                columns=[
                    AnalyticsColumnManifest(
                        name=column,
                        dtype=str(dtype),
                        nullable=frame.get_column(column).null_count() > 0,
                    )
                    for column, dtype in spec.columns
                ],
                minimum_timestamp=minimum,
                maximum_timestamp=maximum,
            )
        )

    manifest = AnalyticsManifest(
        schema_version="1.0",
        analytics_id=result.config.analytics_id,
        generator_version=__version__,
        runtime=analytics_runtime_manifest(),
        source_simulation_id=result.source_manifest.simulation_id,
        source_config_sha256=result.source_manifest.config_sha256,
        source_manifest_sha256=result.source_manifest_sha256,
        analytics_config_sha256=file_sha256(config_path),
        metric_catalog_sha256=file_sha256(catalog_path),
        logical_start_at=result.source_manifest.logical_start_at,
        logical_end_at=result.source_manifest.logical_end_at,
        metric_count=len(result.config.metric_names),
        cohort_count=len(result.config.cohorts),
        contribution_analysis_count=len(result.config.contributions),
        quality_error_count=quality_error_count(result.tables["data_quality_results"]),
        tables=table_manifests,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return manifest


def load_manifest(analytics_dir: str | Path) -> AnalyticsManifest:
    path = Path(analytics_dir) / "manifest.json"
    try:
        return AnalyticsManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AnalyticsIOError(f"cannot read analytics manifest {path}: {exc}") from exc
    except Exception as exc:
        raise AnalyticsIOError(f"invalid analytics manifest {path}: {exc}") from exc


def _safe_analytics_path(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise AnalyticsIOError(f"analytics path escapes its root: {relative_path}")
    return candidate


def load_analytics(analytics_dir: str | Path) -> LoadedAnalytics:
    root = Path(analytics_dir)
    manifest = load_manifest(root)
    tables: AnalyticsFrameMap = {}
    for table in manifest.tables:
        path = _safe_analytics_path(root, table.relative_path)
        if not path.is_file():
            raise AnalyticsIOError(f"missing analytics table: {path}")
        try:
            tables[table.name] = pl.read_parquet(path)
        except Exception as exc:
            raise AnalyticsIOError(f"cannot read analytics table {path}: {exc}") from exc
    return LoadedAnalytics(manifest=manifest, tables=tables)

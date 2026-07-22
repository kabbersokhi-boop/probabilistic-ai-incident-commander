"""Read and write self-validating anomaly-detection artifacts."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

from paic import __version__
from paic.artifacts.lease import artifact_reader
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.detection.config import DetectionConfig
from paic.detection.engine import detection_quality_error_count
from paic.detection.manifest import (
    DetectionColumnManifest,
    DetectionManifest,
    DetectionRuntimeManifest,
    DetectionTableManifest,
)
from paic.detection.schema import DETECTION_TABLE_ORDER, DETECTION_TABLE_SPECS
from paic.detection.types import DetectionBuildResult, DetectionFrameMap, LoadedDetection
from paic.simulator.io import file_sha256


class DetectionIOError(RuntimeError):
    """Raised when a detection artifact cannot be safely read or written."""


def resolved_detection_config_json(config: DetectionConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def detection_runtime_manifest() -> DetectionRuntimeManifest:
    packages: dict[str, str] = {}
    for distribution in ("numpy", "polars", "pyarrow", "pydantic", "PyYAML", "scipy"):
        try:
            packages[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover
            packages[distribution] = "unknown"
    return DetectionRuntimeManifest(
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


def export_detection(
    result: DetectionBuildResult,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> DetectionManifest:
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staging:
            manifest = _export_detection_to_root(result, staging)
            publisher.commit()
            return manifest
    except ArtifactPublicationError as exc:
        raise DetectionIOError(str(exc)) from exc


def _export_detection_to_root(result: DetectionBuildResult, root: Path) -> DetectionManifest:
    table_dir = root / "tables"
    table_dir.mkdir(parents=True, exist_ok=False)
    config_path = root / "detection.config.resolved.json"
    config_path.write_text(resolved_detection_config_json(result.config), encoding="utf-8")

    table_manifests: list[DetectionTableManifest] = []
    for table_name in DETECTION_TABLE_ORDER:
        if table_name not in result.tables:
            raise DetectionIOError(f"missing detection table: {table_name}")
        frame = result.tables[table_name]
        spec = DETECTION_TABLE_SPECS[table_name]
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
            DetectionTableManifest(
                name=table_name,
                relative_path=relative_path.as_posix(),
                row_count=frame.height,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                primary_key=list(spec.primary_key),
                columns=[
                    DetectionColumnManifest(
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
    observations = result.tables["detector_observations"]
    benchmark = result.tables["benchmark_summary"]
    benchmark_row = benchmark.to_dicts()[0] if not benchmark.is_empty() else None
    manifest = DetectionManifest(
        schema_version="1.0",
        detection_id=result.config.detection_id,
        generator_version=__version__,
        runtime=detection_runtime_manifest(),
        source_analytics_id=result.source_manifest.analytics_id,
        source_analytics_manifest_sha256=result.source_manifest_sha256,
        source_analytics_config_sha256=result.source_manifest.analytics_config_sha256,
        detection_config_sha256=file_sha256(config_path),
        logical_start_at=result.source_manifest.logical_start_at,
        logical_end_at=result.source_manifest.logical_end_at,
        selected_metric_count=observations.get_column("metric_name").n_unique(),
        selected_series_count=observations.get_column("series_id").n_unique(),
        observation_count=observations.height,
        anomaly_observation_count=observations.filter(pl.col("is_anomaly")).height,
        anomaly_event_count=result.tables["anomaly_events"].height,
        change_point_count=result.tables["change_point_events"].height,
        benchmark_scenario_count=result.tables["benchmark_ground_truth"].height,
        benchmark_precision=float(benchmark_row["precision"]) if benchmark_row else None,
        benchmark_scenario_recall=float(benchmark_row["scenario_recall"])
        if benchmark_row
        else None,
        benchmark_false_positive_rate=float(benchmark_row["false_positive_rate"])
        if benchmark_row
        else None,
        benchmark_mean_delay_periods=float(benchmark_row["mean_detection_delay_periods"])
        if benchmark_row and benchmark_row["mean_detection_delay_periods"] is not None
        else None,
        quality_error_count=detection_quality_error_count(
            result.tables["detection_quality_results"]
        ),
        tables=table_manifests,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return manifest


def load_manifest(detection_dir: str | Path) -> DetectionManifest:
    path = Path(detection_dir) / "manifest.json"
    try:
        return DetectionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DetectionIOError(f"cannot read detection manifest {path}: {exc}") from exc
    except Exception as exc:
        raise DetectionIOError(f"invalid detection manifest {path}: {exc}") from exc


def _safe_path(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise DetectionIOError(f"detection path escapes its root: {relative_path}")
    return candidate


@artifact_reader
def load_detection(detection_dir: str | Path) -> LoadedDetection:
    root = Path(detection_dir)
    manifest = load_manifest(root)
    tables: DetectionFrameMap = {}
    for table in manifest.tables:
        path = _safe_path(root, table.relative_path)
        if not path.is_file():
            raise DetectionIOError(f"missing detection table: {path}")
        try:
            tables[table.name] = pl.read_parquet(path)
        except Exception as exc:
            raise DetectionIOError(f"cannot read detection table {path}: {exc}") from exc
    return LoadedDetection(manifest=manifest, tables=tables)

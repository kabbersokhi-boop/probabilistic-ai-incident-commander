"""Read and write self-validating customer-impact artifacts."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

from paic import __version__
from paic.impact.config import ImpactConfig
from paic.impact.engine import impact_quality_error_count
from paic.impact.manifest import (
    ImpactColumnManifest,
    ImpactManifest,
    ImpactRuntimeManifest,
    ImpactTableManifest,
)
from paic.impact.schema import IMPACT_TABLE_ORDER, IMPACT_TABLE_SPECS
from paic.impact.types import ImpactBuildResult, ImpactFrameMap, LoadedImpact
from paic.simulator.io import file_sha256


class ImpactIOError(RuntimeError):
    """Raised when impact artifacts cannot be safely read or written."""


def resolved_impact_config_json(config: ImpactConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def impact_runtime_manifest() -> ImpactRuntimeManifest:
    packages: dict[str, str] = {}
    for distribution in ("numpy", "polars", "pyarrow", "pydantic", "PyYAML", "scipy"):
        try:
            packages[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover
            packages[distribution] = "unknown"
    return ImpactRuntimeManifest(
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
        for value in (series.min(), series.max()):
            if isinstance(value, datetime):
                values.append(value)
    return (min(values), max(values)) if values else (None, None)


def export_impact(
    result: ImpactBuildResult, output_dir: str | Path, *, overwrite: bool = False
) -> ImpactManifest:
    root = Path(output_dir)
    if root.exists():
        if not overwrite:
            raise ImpactIOError(f"output directory already exists: {root}")
        if root.is_file():
            raise ImpactIOError(f"output path is a file: {root}")
        shutil.rmtree(root)
    table_dir = root / "tables"
    table_dir.mkdir(parents=True, exist_ok=False)
    config_path = root / "impact.config.resolved.json"
    config_path.write_text(resolved_impact_config_json(result.config), encoding="utf-8")

    table_manifests: list[ImpactTableManifest] = []
    for table_name in IMPACT_TABLE_ORDER:
        frame = result.tables[table_name]
        spec = IMPACT_TABLE_SPECS[table_name]
        relative = Path("tables") / f"{table_name}.parquet"
        path = root / relative
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
            ImpactTableManifest(
                name=table_name,
                relative_path=relative.as_posix(),
                row_count=frame.height,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                primary_key=list(spec.primary_key),
                columns=[
                    ImpactColumnManifest(
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
    features = result.tables["customer_features"]
    manifest = ImpactManifest(
        schema_version="1.0",
        impact_id=result.config.impact_id,
        incident_id=result.config.incident.incident_id,
        generator_version=__version__,
        runtime=impact_runtime_manifest(),
        source_simulation_id=result.source_manifest.simulation_id,
        source_manifest_sha256=result.source_manifest_sha256,
        source_config_sha256=result.source_manifest.config_sha256,
        impact_config_sha256=file_sha256(config_path),
        logical_start_at=result.source_manifest.logical_start_at,
        logical_end_at=result.source_manifest.logical_end_at,
        customer_count=features.height,
        exposed_customer_count=features.filter(pl.col("exposed")).height,
        quality_error_count=impact_quality_error_count(result.tables["impact_quality_results"]),
        tables=table_manifests,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return manifest


def load_manifest(impact_dir: str | Path) -> ImpactManifest:
    path = Path(impact_dir) / "manifest.json"
    try:
        return ImpactManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ImpactIOError(f"cannot read impact manifest {path}: {exc}") from exc
    except Exception as exc:
        raise ImpactIOError(f"invalid impact manifest {path}: {exc}") from exc


def _safe_path(root: Path, relative_path: str) -> Path:
    resolved = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != resolved and resolved not in candidate.parents:
        raise ImpactIOError(f"impact path escapes its root: {relative_path}")
    return candidate


def load_impact(impact_dir: str | Path) -> LoadedImpact:
    root = Path(impact_dir)
    manifest = load_manifest(root)
    tables: ImpactFrameMap = {}
    for table in manifest.tables:
        path = _safe_path(root, table.relative_path)
        if not path.is_file():
            raise ImpactIOError(f"missing impact table: {path}")
        try:
            tables[table.name] = pl.read_parquet(path)
        except Exception as exc:
            raise ImpactIOError(f"cannot read impact table {path}: {exc}") from exc
    return LoadedImpact(manifest=manifest, tables=tables)

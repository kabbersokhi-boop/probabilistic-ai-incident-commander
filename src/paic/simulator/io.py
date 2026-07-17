"""Read and write deterministic simulator datasets."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import polars as pl

from paic import __version__
from paic.simulator.config import SimulationConfig
from paic.simulator.manifest import (
    ColumnManifest,
    DatasetManifest,
    ForeignKeyManifest,
    RuntimeManifest,
    TableManifest,
)
from paic.simulator.schema import TABLE_ORDER, TABLE_SPECS
from paic.simulator.types import FrameMap, SimulationResult


class DatasetIOError(RuntimeError):
    """Raised when simulation artifacts cannot be read or written."""


def canonical_config_hash(config: SimulationConfig) -> str:
    return hashlib.sha256(resolved_config_json(config).encode()).hexdigest()


def resolved_config_json(config: SimulationConfig) -> str:
    """Serialize a resolved configuration once for export and hashing."""

    return json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_manifest() -> RuntimeManifest:
    package_versions: dict[str, str] = {}
    for distribution in ("Faker", "numpy", "polars", "pyarrow", "pydantic", "PyYAML"):
        try:
            package_versions[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover - required dependencies are installed
            package_versions[distribution] = "unknown"
    return RuntimeManifest(
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        platform=sys.platform,
        packages=package_versions,
    )


def export_dataset(
    result: SimulationResult,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> DatasetManifest:
    """Write all tables as Parquet plus a self-validating JSON manifest."""

    root = Path(output_dir)
    if root.exists():
        if not overwrite:
            raise DatasetIOError(f"output directory already exists: {root}")
        if root.is_file():
            raise DatasetIOError(f"output path is a file: {root}")
        shutil.rmtree(root)
    table_dir = root / "tables"
    table_dir.mkdir(parents=True, exist_ok=False)

    resolved_config = root / "config.resolved.json"
    resolved_config.write_text(resolved_config_json(result.config), encoding="utf-8")

    table_manifests: list[TableManifest] = []
    for table_name in TABLE_ORDER:
        frame = result.tables[table_name]
        relative_path = Path("tables") / f"{table_name}.parquet"
        path = root / relative_path
        kwargs: dict[str, object] = {
            "compression": result.config.output.compression,
            "statistics": result.config.output.include_statistics,
            "row_group_size": result.config.output.row_group_size,
        }
        if result.config.output.compression_level is not None:
            kwargs["compression_level"] = result.config.output.compression_level
        frame.write_parquet(path, **kwargs)

        spec = TABLE_SPECS[table_name]
        timestamp_values: list[datetime] = []
        for column in spec.timestamp_columns:
            series = frame.get_column(column).drop_nulls()
            if not series.is_empty():
                for value in (series.min(), series.max()):
                    if isinstance(value, datetime):
                        timestamp_values.append(value)
        minimum_timestamp = min(timestamp_values) if timestamp_values else None
        maximum_timestamp = max(timestamp_values) if timestamp_values else None

        table_manifests.append(
            TableManifest(
                name=table_name,
                relative_path=relative_path.as_posix(),
                row_count=frame.height,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                primary_key=list(spec.primary_key),
                foreign_keys=[
                    ForeignKeyManifest(
                        column=item.column,
                        target_table=item.target_table,
                        target_column=item.target_column,
                        nullable=item.nullable,
                    )
                    for item in spec.foreign_keys
                ],
                columns=[
                    ColumnManifest(
                        name=column,
                        dtype=str(dtype),
                        nullable=frame.get_column(column).null_count() > 0,
                    )
                    for column, dtype in spec.columns
                ],
                minimum_timestamp=minimum_timestamp,
                maximum_timestamp=maximum_timestamp,
            )
        )

    manifest = DatasetManifest(
        schema_version="1.0",
        simulation_id=result.config.simulation_id,
        generator_version=__version__,
        runtime=runtime_manifest(),
        seed=result.config.seed,
        config_sha256=canonical_config_hash(result.config),
        logical_start_at=result.config.start_at,
        logical_end_at=result.config.end_at,
        incident_injections=0,
        tables=table_manifests,
    )
    (root / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "_SUCCESS").write_text(manifest.config_sha256 + "\n", encoding="utf-8")
    return manifest


def load_manifest(dataset_dir: str | Path) -> DatasetManifest:
    path = Path(dataset_dir) / "manifest.json"
    try:
        return DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DatasetIOError(f"cannot read dataset manifest {path}: {exc}") from exc
    except Exception as exc:
        raise DatasetIOError(f"invalid dataset manifest {path}: {exc}") from exc


def _safe_dataset_path(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise DatasetIOError(f"dataset path escapes its root: {relative_path}")
    return candidate


def load_dataset(dataset_dir: str | Path) -> tuple[DatasetManifest, FrameMap]:
    root = Path(dataset_dir)
    manifest = load_manifest(root)
    tables: FrameMap = {}
    for table in manifest.tables:
        path = _safe_dataset_path(root, table.relative_path)
        if not path.is_file():
            raise DatasetIOError(f"missing dataset table: {path}")
        try:
            tables[table.name] = pl.read_parquet(path)
        except Exception as exc:
            raise DatasetIOError(f"cannot read dataset table {path}: {exc}") from exc
    return manifest, tables

"""Read and write self-validating operational evidence artifacts."""

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
from paic.evidence.config import EvidenceConfig
from paic.evidence.engine import evidence_quality_error_count
from paic.evidence.manifest import (
    EvidenceColumnManifest,
    EvidenceManifest,
    EvidenceRuntimeManifest,
    EvidenceTableManifest,
)
from paic.evidence.schema import EVIDENCE_TABLE_ORDER, EVIDENCE_TABLE_SPECS
from paic.evidence.types import EvidenceBuildResult, EvidenceFrameMap, LoadedEvidence
from paic.simulator.io import file_sha256


class EvidenceIOError(RuntimeError):
    """Raised when evidence artifacts cannot be safely read or written."""


def resolved_evidence_config_json(config: EvidenceConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def evidence_runtime_manifest() -> EvidenceRuntimeManifest:
    packages: dict[str, str] = {}
    for distribution in ("numpy", "polars", "pyarrow", "pydantic", "PyYAML", "scipy"):
        try:
            packages[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover
            packages[distribution] = "unknown"
    return EvidenceRuntimeManifest(
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
        if column not in frame.columns:
            continue
        series = frame.get_column(column).drop_nulls()
        if series.is_empty():
            continue
        for value in (series.min(), series.max()):
            if isinstance(value, datetime):
                values.append(value)
    return (min(values), max(values)) if values else (None, None)


def export_evidence(
    result: EvidenceBuildResult,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> EvidenceManifest:
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staging:
            manifest = _export_evidence_to_root(result, staging)
            publisher.commit()
            return manifest
    except ArtifactPublicationError as exc:
        raise EvidenceIOError(str(exc)) from exc


def _export_evidence_to_root(result: EvidenceBuildResult, root: Path) -> EvidenceManifest:
    table_dir = root / "tables"
    table_dir.mkdir(parents=True, exist_ok=False)
    config_path = root / "evidence.config.resolved.json"
    config_path.write_text(resolved_evidence_config_json(result.config), encoding="utf-8")
    table_manifests: list[EvidenceTableManifest] = []
    for name in EVIDENCE_TABLE_ORDER:
        frame = result.tables[name]
        spec = EVIDENCE_TABLE_SPECS[name]
        relative = Path("tables") / f"{name}.parquet"
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
            EvidenceTableManifest(
                name=name,
                relative_path=relative.as_posix(),
                row_count=frame.height,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                primary_key=list(spec.primary_key),
                columns=[
                    EvidenceColumnManifest(
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
    manifest = EvidenceManifest(
        schema_version="1.0",
        evidence_id=result.config.evidence_id,
        incident_id=result.config.incident.incident_id,
        generator_version=__version__,
        runtime=evidence_runtime_manifest(),
        source_dataset_id=result.source_dataset_manifest.simulation_id,
        source_dataset_manifest_sha256=result.source_dataset_manifest_sha256,
        source_analytics_manifest_sha256=result.source_analytics_manifest_sha256,
        source_detection_manifest_sha256=result.source_detection_manifest_sha256,
        source_impact_manifest_sha256=result.source_impact_manifest_sha256,
        evidence_config_sha256=file_sha256(config_path),
        logical_start_at=result.source_dataset_manifest.logical_start_at,
        logical_end_at=result.source_dataset_manifest.logical_end_at,
        evidence_record_count=result.tables["evidence_records"].height,
        timeline_event_count=result.tables["incident_timeline"].height,
        lineage_node_count=result.tables["lineage_nodes"].height,
        lineage_edge_count=result.tables["lineage_edges"].height,
        quality_error_count=evidence_quality_error_count(result.tables["evidence_quality_results"]),
        tables=table_manifests,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return manifest


def load_manifest(evidence_dir: str | Path) -> EvidenceManifest:
    path = Path(evidence_dir) / "manifest.json"
    try:
        return EvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvidenceIOError(f"cannot read evidence manifest {path}: {exc}") from exc
    except Exception as exc:
        raise EvidenceIOError(f"invalid evidence manifest {path}: {exc}") from exc


def _safe_path(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise EvidenceIOError(f"evidence path escapes its root: {relative_path}")
    return candidate


@artifact_reader
def load_evidence(evidence_dir: str | Path) -> LoadedEvidence:
    root = Path(evidence_dir)
    manifest = load_manifest(root)
    tables: EvidenceFrameMap = {}
    for table in manifest.tables:
        path = _safe_path(root, table.relative_path)
        if not path.is_file():
            raise EvidenceIOError(f"missing evidence table: {path}")
        try:
            tables[table.name] = pl.read_parquet(path)
        except Exception as exc:
            raise EvidenceIOError(f"cannot read evidence table {path}: {exc}") from exc
    return LoadedEvidence(manifest=manifest, tables=tables)

"""Closed-world source-bound synthetic recovery-observation artifacts.

Post-action values are deterministic evaluator fixtures, never production telemetry.
Their authority is the resolved scenario plus validated analytics and execution artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paic import __version__
from paic.analytics.io import load_analytics, load_manifest
from paic.analytics.registry import metric_catalog
from paic.artifacts.lease import artifact_reader
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.recovery.artifact import file_sha256
from paic.recovery.manifest import ObservationArtifactFile, ObservationArtifactManifest
from paic.recovery.models import Identifier, RecoveryObservationSet
from paic.remediation.artifact import load_execution, manifest_sha256


class ObservationError(RuntimeError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SyntheticSeries(_Strict):
    metric_id: Identifier
    cohort: Identifier
    values: Annotated[list[float], Field(min_length=1, max_length=10_000)]
    sample_size: int = Field(ge=1)


class ObservationScenario(_Strict):
    schema_version: str = "1.0"
    observation_set_id: Identifier
    generated_at_offset_hours: int = Field(ge=0, le=100_000)
    post_interval_hours: int = Field(default=1, ge=1, le=10_000)
    series: Annotated[list[SyntheticSeries], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def unique_series(self) -> ObservationScenario:
        keys = [(item.metric_id, item.cohort) for item in self.series]
        if len(keys) != len(set(keys)):
            raise ValueError("synthetic observation scenario series must be unique")
        return self


EXPECTED = {"observation.config.resolved.json", "observation-set.json", "manifest.json", "_SUCCESS"}
_GRAIN_RANK = {"hour": 1, "day": 2, "week": 3, "month": 4, "quarter": 5, "year": 6}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _write(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _derive_observations(
    scenario: ObservationScenario,
    analytics_dir: str | Path,
    execution_dir: str | Path,
) -> RecoveryObservationSet:
    """Reconstruct the only observation payload authorized by the inputs."""
    analytics = load_analytics(analytics_dir)
    analytics_manifest = load_manifest(analytics_dir)
    execution = load_execution(execution_dir)
    metric_ids = {str(item["name"]) for item in metric_catalog()}
    requested = {(item.metric_id, item.cohort) for item in scenario.series}
    if any(metric not in metric_ids for metric, _ in requested):
        raise ObservationError("scenario references an unknown analytics metric")
    table = analytics.tables["metric_observations"]
    available = {
        (str(row["metric_name"]), str(row["cohort_name"]))
        for row in table.select(["metric_name", "cohort_name"]).unique().iter_rows(named=True)
    }
    if not requested.issubset(available):
        raise ObservationError("scenario series is absent from bound analytics artifact")
    rows: list[dict[str, object]] = []
    for series in scenario.series:
        selected = table.filter(
            (pl.col("metric_name") == series.metric_id) & (pl.col("cohort_name") == series.cohort)
        ).sort("period_start")
        if "time_grain" in selected.columns:
            # Analytics may publish multiple grains at the same timestamp.  A
            # recovery series has one deterministic grain: the coarsest one.
            grain = max(
                selected.get_column("time_grain").unique().to_list(),
                key=lambda value: (_GRAIN_RANK.get(str(value), 0), str(value)),
            )
            selected = selected.filter(pl.col("time_grain") == grain)
        selected = selected.filter(pl.col("period_start") < execution.receipt.executed_at)
        for row in selected.iter_rows(named=True):
            rows.append(
                {
                    "metric_id": series.metric_id,
                    "cohort": series.cohort,
                    "observed_at": row["period_start"],
                    "value": row["value"],
                    "sample_size": int(row["sample_size"]),
                }
            )
        for index, value in enumerate(series.values, start=1):
            rows.append(
                {
                    "metric_id": series.metric_id,
                    "cohort": series.cohort,
                    "observed_at": execution.receipt.executed_at
                    + timedelta(hours=index * scenario.post_interval_hours),
                    "value": value,
                    "sample_size": series.sample_size,
                }
            )
    generated_at = execution.receipt.executed_at + timedelta(
        hours=scenario.generated_at_offset_hours
    )
    return RecoveryObservationSet.model_validate(
        {
            "observation_set_id": scenario.observation_set_id,
            "incident_id": execution.receipt.incident_id,
            "execution_receipt_sha256": execution.receipt.receipt_sha256,
            "execution_manifest_sha256": manifest_sha256(execution_dir),
            "analytics_manifest_sha256": file_sha256(Path(analytics_dir) / "manifest.json"),
            "source_simulation_id": analytics_manifest.source_simulation_id,
            "generator_config_sha256": _digest(scenario.model_dump(mode="json")),
            "executed_at": execution.receipt.executed_at,
            "generated_at": generated_at,
            "observations": rows,
        }
    )


def build_observations(
    scenario: ObservationScenario,
    analytics_dir: str | Path,
    execution_dir: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> RecoveryObservationSet:
    """Derive baseline rows from validated analytics and post rows from a strict scenario."""
    observation_set = _derive_observations(scenario, analytics_dir, execution_dir)
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staged:
            _write(
                staged / "observation.config.resolved.json",
                json.dumps(scenario.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            )
            _write(
                staged / "observation-set.json", observation_set.model_dump_json(indent=2) + "\n"
            )
            manifest = ObservationArtifactManifest(
                observation_set_id=observation_set.observation_set_id,
                incident_id=observation_set.incident_id,
                generator_version=__version__,
                analytics_manifest_sha256=observation_set.analytics_manifest_sha256,
                execution_manifest_sha256=observation_set.execution_manifest_sha256,
                execution_receipt_sha256=observation_set.execution_receipt_sha256,
                generator_config_sha256=observation_set.generator_config_sha256,
                files=[
                    ObservationArtifactFile(
                        relative_path=name,
                        byte_size=(staged / name).stat().st_size,
                        sha256=file_sha256(staged / name),
                    )
                    for name in ("observation.config.resolved.json", "observation-set.json")
                ],
            )
            _write(staged / "manifest.json", manifest.model_dump_json(indent=2) + "\n")
            _write(staged / "_SUCCESS", file_sha256(staged / "manifest.json") + "\n")
            load_observations(staged, analytics_dir=analytics_dir, execution_dir=execution_dir)
            publisher.commit()
    except ArtifactPublicationError as exc:
        raise ObservationError(str(exc)) from exc
    return observation_set


@artifact_reader
def load_observations(
    path: str | Path,
    *,
    analytics_dir: str | Path | None = None,
    execution_dir: str | Path | None = None,
) -> RecoveryObservationSet:
    root = Path(path)
    if root.is_symlink() or not root.is_dir() or {item.name for item in root.iterdir()} != EXPECTED:
        raise ObservationError("observation artifact contains missing or undeclared paths")
    if any(item.is_symlink() or not item.is_file() for item in root.iterdir()):
        raise ObservationError("observation artifact contains non-regular paths")
    try:
        manifest = ObservationArtifactManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ObservationError(f"invalid observation manifest: {exc}") from exc
    if (root / "_SUCCESS").read_text(encoding="utf-8").strip() != file_sha256(
        root / "manifest.json"
    ):
        raise ObservationError("observation success marker mismatch")
    for item in manifest.files:
        target = root / item.relative_path
        if target.stat().st_size != item.byte_size or file_sha256(target) != item.sha256:
            raise ObservationError("observation manifest file hash mismatch (or size mismatch)")
    scenario = ObservationScenario.model_validate_json(
        (root / "observation.config.resolved.json").read_text(encoding="utf-8")
    )
    observations = RecoveryObservationSet.model_validate_json(
        (root / "observation-set.json").read_text(encoding="utf-8")
    )
    if observations.generator_config_sha256 != _digest(scenario.model_dump(mode="json")):
        raise ObservationError("observation generator configuration hash mismatch")
    if (
        manifest.observation_set_id != observations.observation_set_id
        or manifest.incident_id != observations.incident_id
        or manifest.evaluator_generated != observations.evaluator_generated
        or manifest.analytics_manifest_sha256 != observations.analytics_manifest_sha256
        or manifest.execution_manifest_sha256 != observations.execution_manifest_sha256
        or manifest.execution_receipt_sha256 != observations.execution_receipt_sha256
        or manifest.generator_config_sha256 != observations.generator_config_sha256
    ):
        raise ObservationError("observation manifest bindings differ from payload")
    if manifest.generator_version != __version__:
        raise ObservationError("observation manifest generator version differs from package")
    if execution_dir is not None:
        execution = load_execution(execution_dir)
        if (
            observations.incident_id != execution.receipt.incident_id
            or observations.executed_at != execution.receipt.executed_at
            or observations.execution_receipt_sha256 != execution.receipt.receipt_sha256
            or observations.execution_manifest_sha256 != manifest_sha256(execution_dir)
        ):
            raise ObservationError("observation artifact is bound to another execution")
    if analytics_dir is not None:
        if execution_dir is None:
            raise ObservationError(
                "analytics-bound observation replay requires the bound execution artifact"
            )
        analytics = load_analytics(analytics_dir)
        analytics_manifest = load_manifest(analytics_dir)
        if observations.analytics_manifest_sha256 != file_sha256(
            Path(analytics_dir) / "manifest.json"
        ):
            raise ObservationError("observation artifact is bound to another analytics manifest")
        if observations.source_simulation_id != analytics_manifest.source_simulation_id:
            raise ObservationError("observation artifact is bound to another source dataset")
        available = {
            (str(row["metric_name"]), str(row["cohort_name"]))
            for row in analytics.tables["metric_observations"]
            .select(["metric_name", "cohort_name"])
            .unique()
            .iter_rows(named=True)
        }
        if not {(item.metric_id, item.cohort) for item in observations.observations}.issubset(
            available
        ):
            raise ObservationError("observation artifact contains an unknown analytics series")
        expected = _derive_observations(scenario, analytics_dir, execution_dir)
        if observations != expected:
            raise ObservationError("observation payload is not reproducible from bound sources")
    return observations


def observation_manifest_sha256(path: str | Path) -> str:
    return file_sha256(Path(path) / "manifest.json")


def validate_observations(
    path: str | Path,
    *,
    analytics_dir: str | Path | None = None,
    execution_dir: str | Path | None = None,
) -> list[str]:
    try:
        load_observations(path, analytics_dir=analytics_dir, execution_dir=execution_dir)
    except (ObservationError, OSError, ValueError) as exc:
        return [str(exc)]
    return []

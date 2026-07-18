"""Manifest models for exported anomaly-detection artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DetectionColumnManifest(StrictModel):
    name: NonEmptyText
    dtype: NonEmptyText
    nullable: bool


class DetectionTableManifest(StrictModel):
    name: NonEmptyText
    relative_path: NonEmptyText
    row_count: Annotated[int, Field(ge=0)]
    byte_size: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    primary_key: list[NonEmptyText]
    columns: list[DetectionColumnManifest]
    minimum_timestamp: datetime | None = None
    maximum_timestamp: datetime | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("relative_path must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("relative_path must stay inside the detection directory")
        return value

    @model_validator(mode="after")
    def validate_metadata(self) -> DetectionTableManifest:
        names = [item.name for item in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("manifest column names must be unique")
        if len(self.primary_key) != len(set(self.primary_key)):
            raise ValueError("manifest primary-key columns must be unique")
        unknown = set(self.primary_key).difference(names)
        if unknown:
            raise ValueError(f"manifest primary key references unknown columns: {sorted(unknown)}")
        if (
            self.minimum_timestamp is not None
            and self.maximum_timestamp is not None
            and self.minimum_timestamp > self.maximum_timestamp
        ):
            raise ValueError("minimum_timestamp must not be after maximum_timestamp")
        return self


class DetectionRuntimeManifest(StrictModel):
    python_version: NonEmptyText
    python_implementation: NonEmptyText
    platform: NonEmptyText
    packages: dict[NonEmptyText, NonEmptyText]


class DetectionManifest(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    detection_id: NonEmptyText
    generator_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    runtime: DetectionRuntimeManifest
    source_analytics_id: NonEmptyText
    source_analytics_manifest_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source_analytics_config_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    detection_config_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    logical_start_at: datetime
    logical_end_at: datetime
    selected_metric_count: Annotated[int, Field(ge=1)]
    selected_series_count: Annotated[int, Field(ge=1)]
    observation_count: Annotated[int, Field(ge=0)]
    anomaly_observation_count: Annotated[int, Field(ge=0)]
    anomaly_event_count: Annotated[int, Field(ge=0)]
    change_point_count: Annotated[int, Field(ge=0)]
    benchmark_scenario_count: Annotated[int, Field(ge=0)]
    benchmark_precision: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    benchmark_scenario_recall: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    benchmark_false_positive_rate: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    benchmark_mean_delay_periods: Annotated[float, Field(ge=0.0)] | None = None
    quality_error_count: Annotated[int, Field(ge=0)]
    tables: list[DetectionTableManifest]

    @model_validator(mode="after")
    def validate_manifest(self) -> DetectionManifest:
        if self.logical_end_at <= self.logical_start_at:
            raise ValueError("logical_end_at must be after logical_start_at")
        names = [item.name for item in self.tables]
        paths = [item.relative_path for item in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("manifest table names must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest table paths must be unique")
        if self.anomaly_observation_count > self.observation_count:
            raise ValueError("anomaly observations cannot exceed observations")
        return self

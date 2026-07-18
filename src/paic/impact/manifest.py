"""Manifest models for exported customer-impact artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImpactColumnManifest(StrictModel):
    name: NonEmptyText
    dtype: NonEmptyText
    nullable: bool


class ImpactTableManifest(StrictModel):
    name: NonEmptyText
    relative_path: NonEmptyText
    row_count: Annotated[int, Field(ge=0)]
    byte_size: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    primary_key: list[NonEmptyText]
    columns: list[ImpactColumnManifest]
    minimum_timestamp: datetime | None = None
    maximum_timestamp: datetime | None = None

    @field_validator("relative_path")
    @classmethod
    def relative_path_is_safe(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("relative_path must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("relative_path must stay inside the impact directory")
        return value

    @model_validator(mode="after")
    def metadata_is_consistent(self) -> ImpactTableManifest:
        names = [item.name for item in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("manifest column names must be unique")
        if set(self.primary_key).difference(names):
            raise ValueError("manifest primary key references unknown columns")
        return self


class ImpactRuntimeManifest(StrictModel):
    python_version: NonEmptyText
    python_implementation: NonEmptyText
    platform: NonEmptyText
    packages: dict[NonEmptyText, NonEmptyText]


class ImpactManifest(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    impact_id: NonEmptyText
    incident_id: NonEmptyText
    generator_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    runtime: ImpactRuntimeManifest
    source_simulation_id: NonEmptyText
    source_manifest_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source_config_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    impact_config_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    logical_start_at: datetime
    logical_end_at: datetime
    customer_count: Annotated[int, Field(ge=1)]
    exposed_customer_count: Annotated[int, Field(ge=1)]
    quality_error_count: Annotated[int, Field(ge=0)]
    tables: list[ImpactTableManifest]

    @model_validator(mode="after")
    def manifest_is_consistent(self) -> ImpactManifest:
        if self.logical_end_at <= self.logical_start_at:
            raise ValueError("logical_end_at must be after logical_start_at")
        names = [item.name for item in self.tables]
        paths = [item.relative_path for item in self.tables]
        if len(names) != len(set(names)) or len(paths) != len(set(paths)):
            raise ValueError("manifest table names and paths must be unique")
        if self.exposed_customer_count > self.customer_count:
            raise ValueError("exposed customer count cannot exceed customer count")
        return self

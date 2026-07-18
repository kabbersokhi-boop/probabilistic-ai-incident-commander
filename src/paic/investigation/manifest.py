"""Manifest for exported investigation artifacts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvestigationFileManifest(StrictModel):
    relative_path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the artifact")
        return value


class InvestigationManifest(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    investigation_id: str
    incident_id: str
    generator_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    status: str
    selected_hypothesis_id: str | None
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifest_hashes: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]]
    model_attempt_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    transcript_event_count: int = Field(ge=0)
    files: list[InvestigationFileManifest]

    @model_validator(mode="after")
    def unique_files(self) -> InvestigationManifest:
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest file paths must be unique")
        return self

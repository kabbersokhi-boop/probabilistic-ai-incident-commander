"""Manifests for remediation, control-state, and execution artifacts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactFileManifest(StrictModel):
    relative_path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the artifact")
        if path.name != value:
            raise ValueError("remediation artifacts must remain flat")
        return value


class RemediationArtifactManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["control_state", "remediation_plan", "execution_receipt"]
    artifact_id: str = Field(min_length=1, max_length=200)
    incident_id: str = Field(min_length=1, max_length=200)
    generator_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    status: str = Field(min_length=1, max_length=100)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bindings: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]]
    files: list[ArtifactFileManifest]

    @model_validator(mode="after")
    def unique_files(self) -> RemediationArtifactManifest:
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest file paths must be unique")
        return self

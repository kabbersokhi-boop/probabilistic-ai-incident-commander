"""Manifest models for closed-world recovery artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from paic.recovery.models import Identifier, Sha256


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecoveryArtifactFile(StrictModel):
    relative_path: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    byte_size: int = Field(ge=0)
    sha256: Sha256


class RecoveryArtifactManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["recovery_report"] = "recovery_report"
    artifact_id: Identifier
    incident_id: Identifier
    generator_version: str = Field(min_length=1)
    status: Literal["recovered", "recovering", "failed", "insufficient_data"]
    payload_sha256: Sha256
    bindings: dict[str, Sha256]
    files: list[RecoveryArtifactFile] = Field(min_length=4, max_length=10)

    @model_validator(mode="after")
    def unique_files(self) -> RecoveryArtifactManifest:
        names = [item.relative_path for item in self.files]
        if len(names) != len(set(names)):
            raise ValueError("recovery artifact files must be unique")
        return self

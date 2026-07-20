"""Strict models shared by the terminal interface."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricPipelinePaths(StrictModel):
    dataset_dir: Path | None = None
    analytics_dir: Path | None = None
    detection_dir: Path | None = None


class IncidentPipelinePaths(StrictModel):
    dataset_dir: Path | None = None
    analytics_dir: Path | None = None
    detection_dir: Path | None = None
    impact_dir: Path | None = None
    evidence_dir: Path | None = None
    investigation_dir: Path | None = None
    investigation_config: Path | None = None


class RemediationPaths(StrictModel):
    plan_dir: Path | None = None
    state_before_dir: Path | None = None
    state_after_dir: Path | None = None
    execution_dir: Path | None = None


class RecoveryPaths(StrictModel):
    observations_dir: Path | None = None
    analytics_dir: Path | None = None
    report_dir: Path | None = None


class EvaluationPaths(StrictModel):
    run_dir: Path | None = None
    visible_dir: Path | None = None
    answers_dir: Path | None = None
    predictions: Path | None = None
    config: Path | None = None


class WorkspacePaths(StrictModel):
    metrics: MetricPipelinePaths = Field(default_factory=MetricPipelinePaths)
    incident: IncidentPipelinePaths = Field(default_factory=IncidentPipelinePaths)
    remediation: RemediationPaths = Field(default_factory=RemediationPaths)
    recovery: RecoveryPaths = Field(default_factory=RecoveryPaths)
    evaluation: EvaluationPaths = Field(default_factory=EvaluationPaths)


class WorkspaceConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=120)
    root_dir: Path = Path(".")
    paths: WorkspacePaths


StageStatus = Literal["healthy", "warning", "error", "missing", "not_configured"]


class StageSnapshot(StrictModel):
    key: str
    title: str
    status: StageStatus
    summary: str
    path: str | None = None
    authoritative: bool = False
    details: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class WorkspaceSnapshot(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str
    display_name: str
    root_dir: str
    overall_status: StageStatus
    configured_stage_count: int = Field(ge=0)
    healthy_stage_count: int = Field(ge=0)
    stages: list[StageSnapshot]

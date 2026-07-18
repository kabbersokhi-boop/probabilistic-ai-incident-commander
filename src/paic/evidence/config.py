"""Strict configuration for operational evidence and lineage artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
EvidenceRole = Literal["supporting", "contradictory", "context"]


class EvidenceConfigError(RuntimeError):
    """Raised when an evidence configuration cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncidentContext(StrictModel):
    incident_id: Slug
    family: NonEmptyText
    started_at: datetime
    ended_at: datetime
    primary_service: NonEmptyText
    region: NonEmptyText | None = None
    context_before_hours: Annotated[int, Field(ge=1, le=720)] = 24
    context_after_hours: Annotated[int, Field(ge=1, le=720)] = 24

    @model_validator(mode="after")
    def validate_window(self) -> IncidentContext:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("incident timestamps must be timezone-aware")
        if self.ended_at <= self.started_at:
            raise ValueError("incident ended_at must be after started_at")
        if self.ended_at - self.started_at > timedelta(days=30):
            raise ValueError("incident windows longer than 30 days are not supported")
        return self


class ConfigChange(StrictModel):
    change_id: Slug
    service: NonEmptyText
    key: NonEmptyText
    old_value: str
    new_value: str
    changed_at: datetime
    actor: NonEmptyText
    change_type: Literal["deployment", "configuration", "schema", "routing", "policy"]
    region_scope: NonEmptyText = "global"
    approved: bool = True
    rollback_available: bool = True
    role: EvidenceRole = "context"
    description: NonEmptyText


class FeatureFlagTransition(StrictModel):
    transition_id: Slug
    flag_key: NonEmptyText
    service: NonEmptyText
    value: str
    valid_from: datetime
    valid_to: datetime | None = None
    changed_by: NonEmptyText
    region_scope: NonEmptyText = "global"
    role: EvidenceRole = "context"
    description: NonEmptyText

    @model_validator(mode="after")
    def validate_interval(self) -> FeatureFlagTransition:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("feature flag valid_to must be after valid_from")
        return self


class Runbook(StrictModel):
    runbook_id: Slug
    title: NonEmptyText
    service: NonEmptyText
    trigger: NonEmptyText
    diagnostic_steps: list[NonEmptyText]
    remediation_steps: list[NonEmptyText]
    rollback_steps: list[NonEmptyText]
    owner: NonEmptyText
    version: NonEmptyText

    @model_validator(mode="after")
    def validate_steps(self) -> Runbook:
        if not self.diagnostic_steps or not self.remediation_steps:
            raise ValueError("runbooks require diagnostic and remediation steps")
        return self


class HistoricalIncident(StrictModel):
    historical_incident_id: Slug
    family: NonEmptyText
    started_at: datetime
    ended_at: datetime
    root_cause: NonEmptyText
    symptoms: list[NonEmptyText]
    remediation: NonEmptyText
    recovery_minutes: Annotated[int, Field(ge=0)]
    tags: list[NonEmptyText] = Field(default_factory=list)
    service: NonEmptyText

    @model_validator(mode="after")
    def validate_window(self) -> HistoricalIncident:
        if self.ended_at <= self.started_at:
            raise ValueError("historical incident ended_at must be after started_at")
        return self


class ServiceDefinition(StrictModel):
    service: NonEmptyText
    component: NonEmptyText
    owner: NonEmptyText
    source_table: NonEmptyText
    health_kind: Literal["checkout", "payments", "pipeline", "fulfilment", "seller_feed"]
    criticality: Literal["low", "medium", "high", "critical"] = "high"


class LineageNodeDefinition(StrictModel):
    node_id: Slug
    node_type: Literal["source", "service", "dataset", "metric", "model", "report", "control"]
    name: NonEmptyText
    domain: NonEmptyText
    owner: NonEmptyText
    system: NonEmptyText
    description: NonEmptyText


class LineageEdgeDefinition(StrictModel):
    edge_id: Slug
    upstream_node_id: Slug
    downstream_node_id: Slug
    edge_type: Literal["reads", "writes", "derives", "serves", "controls", "validates"]
    transform_name: NonEmptyText
    criticality: Literal["low", "medium", "high", "critical"] = "high"
    valid_from: datetime


class OutputConfig(StrictModel):
    compression: Literal["zstd", "snappy", "gzip", "lz4", "uncompressed"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 6
    row_group_size: Annotated[int, Field(ge=100)] = 25_000
    include_statistics: bool = True

    @model_validator(mode="after")
    def validate_compression(self) -> OutputConfig:
        if self.compression in {"snappy", "lz4", "uncompressed"} and self.compression_level:
            raise ValueError(f"{self.compression} does not accept compression_level")
        if self.compression == "gzip" and (self.compression_level or 0) > 9:
            raise ValueError("gzip compression_level must be between 1 and 9")
        return self


class EvidenceConfig(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    evidence_id: Slug
    timezone: Literal["UTC"] = "UTC"
    incident: IncidentContext
    services: list[ServiceDefinition]
    config_changes: list[ConfigChange] = Field(default_factory=list)
    feature_flags: list[FeatureFlagTransition] = Field(default_factory=list)
    runbooks: list[Runbook] = Field(default_factory=list)
    historical_incidents: list[HistoricalIncident] = Field(default_factory=list)
    lineage_nodes: list[LineageNodeDefinition] = Field(default_factory=list)
    lineage_edges: list[LineageEdgeDefinition] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def validate_references(self) -> EvidenceConfig:
        service_names = [item.service for item in self.services]
        if not service_names or len(service_names) != len(set(service_names)):
            raise ValueError("services must be non-empty and unique")
        node_ids = [item.node_id for item in self.lineage_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("lineage node IDs must be unique")
        known = set(node_ids)
        edge_ids = [item.edge_id for item in self.lineage_edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("lineage edge IDs must be unique")
        for edge in self.lineage_edges:
            if edge.upstream_node_id not in known or edge.downstream_node_id not in known:
                raise ValueError(f"lineage edge {edge.edge_id} references an unknown node")
            if edge.upstream_node_id == edge.downstream_node_id:
                raise ValueError("lineage self-edges are not allowed")
        return self


def load_evidence_config(path: str | Path) -> EvidenceConfig:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise EvidenceConfigError(f"cannot read evidence config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise EvidenceConfigError(f"invalid YAML in evidence config {config_path}: {exc}") from exc
    try:
        return EvidenceConfig.model_validate(raw)
    except Exception as exc:
        raise EvidenceConfigError(f"invalid evidence config {config_path}: {exc}") from exc

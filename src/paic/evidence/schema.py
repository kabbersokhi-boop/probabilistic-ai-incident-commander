"""Canonical table schemas for operational evidence artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.simulator.schema import UTC_DATETIME


@dataclass(frozen=True)
class EvidenceTableSpec:
    name: str
    columns: tuple[tuple[str, pl.DataType], ...]
    primary_key: tuple[str, ...]
    timestamp_columns: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return dict(self.columns)


EVIDENCE_TABLE_SPECS: dict[str, EvidenceTableSpec] = {
    "evidence_records": EvidenceTableSpec(
        name="evidence_records",
        columns=(
            ("evidence_record_id", pl.String),
            ("evidence_type", pl.String),
            ("source_name", pl.String),
            ("source_ref", pl.String),
            ("observed_at", UTC_DATETIME),
            ("valid_from", UTC_DATETIME),
            ("valid_to", UTC_DATETIME),
            ("service", pl.String),
            ("component", pl.String),
            ("region", pl.String),
            ("environment", pl.String),
            ("severity", pl.String),
            ("trust_level", pl.String),
            ("evidence_role", pl.String),
            ("title", pl.String),
            ("summary", pl.String),
            ("payload_json", pl.String),
            ("content_sha256", pl.String),
            ("incident_id", pl.String),
        ),
        primary_key=("evidence_record_id",),
        timestamp_columns=("observed_at", "valid_from", "valid_to"),
    ),
    "config_changes": EvidenceTableSpec(
        name="config_changes",
        columns=(
            ("change_id", pl.String),
            ("change_type", pl.String),
            ("service", pl.String),
            ("key", pl.String),
            ("old_value", pl.String),
            ("new_value", pl.String),
            ("region_scope", pl.String),
            ("changed_at", UTC_DATETIME),
            ("actor", pl.String),
            ("approved", pl.Boolean),
            ("rollback_available", pl.Boolean),
            ("evidence_role", pl.String),
            ("description", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("change_id",),
        timestamp_columns=("changed_at",),
    ),
    "feature_flag_states": EvidenceTableSpec(
        name="feature_flag_states",
        columns=(
            ("flag_state_id", pl.String),
            ("flag_key", pl.String),
            ("value", pl.String),
            ("service", pl.String),
            ("region_scope", pl.String),
            ("valid_from", UTC_DATETIME),
            ("valid_to", UTC_DATETIME),
            ("changed_by", pl.String),
            ("evidence_role", pl.String),
            ("description", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("flag_state_id",),
        timestamp_columns=("valid_from", "valid_to"),
    ),
    "service_health": EvidenceTableSpec(
        name="service_health",
        columns=(
            ("health_observation_id", pl.String),
            ("service", pl.String),
            ("component", pl.String),
            ("region", pl.String),
            ("observed_at", UTC_DATETIME),
            ("window_minutes", pl.Int64),
            ("request_count", pl.Int64),
            ("error_count", pl.Int64),
            ("error_rate", pl.Float64),
            ("p50_latency_ms", pl.Float64),
            ("p95_latency_ms", pl.Float64),
            ("saturation", pl.Float64),
            ("status", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("health_observation_id",),
        timestamp_columns=("observed_at",),
    ),
    "lineage_nodes": EvidenceTableSpec(
        name="lineage_nodes",
        columns=(
            ("node_id", pl.String),
            ("node_type", pl.String),
            ("name", pl.String),
            ("domain", pl.String),
            ("owner", pl.String),
            ("system", pl.String),
            ("description", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("node_id",),
    ),
    "lineage_edges": EvidenceTableSpec(
        name="lineage_edges",
        columns=(
            ("edge_id", pl.String),
            ("upstream_node_id", pl.String),
            ("downstream_node_id", pl.String),
            ("edge_type", pl.String),
            ("transform_name", pl.String),
            ("criticality", pl.String),
            ("valid_from", UTC_DATETIME),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("edge_id",),
        timestamp_columns=("valid_from",),
    ),
    "incident_timeline": EvidenceTableSpec(
        name="incident_timeline",
        columns=(
            ("timeline_event_id", pl.String),
            ("incident_id", pl.String),
            ("occurred_at", UTC_DATETIME),
            ("sequence", pl.Int64),
            ("event_type", pl.String),
            ("source", pl.String),
            ("title", pl.String),
            ("detail", pl.String),
            ("evidence_record_ids_json", pl.String),
        ),
        primary_key=("timeline_event_id",),
        timestamp_columns=("occurred_at",),
    ),
    "historical_incidents": EvidenceTableSpec(
        name="historical_incidents",
        columns=(
            ("historical_incident_id", pl.String),
            ("family", pl.String),
            ("service", pl.String),
            ("started_at", UTC_DATETIME),
            ("ended_at", UTC_DATETIME),
            ("root_cause", pl.String),
            ("symptoms_json", pl.String),
            ("remediation", pl.String),
            ("recovery_minutes", pl.Int64),
            ("tags_json", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("historical_incident_id",),
        timestamp_columns=("started_at", "ended_at"),
    ),
    "runbooks": EvidenceTableSpec(
        name="runbooks",
        columns=(
            ("runbook_id", pl.String),
            ("title", pl.String),
            ("service", pl.String),
            ("trigger", pl.String),
            ("diagnostic_steps_json", pl.String),
            ("remediation_steps_json", pl.String),
            ("rollback_steps_json", pl.String),
            ("owner", pl.String),
            ("version", pl.String),
            ("evidence_record_id", pl.String),
        ),
        primary_key=("runbook_id",),
    ),
    "evidence_quality_results": EvidenceTableSpec(
        name="evidence_quality_results",
        columns=(
            ("check_name", pl.String),
            ("category", pl.String),
            ("severity", pl.String),
            ("status", pl.String),
            ("observed_value", pl.Float64),
            ("expected", pl.String),
            ("details", pl.String),
        ),
        primary_key=("check_name",),
    ),
}

EVIDENCE_TABLE_ORDER: tuple[str, ...] = tuple(EVIDENCE_TABLE_SPECS)


def empty_evidence_frame(table_name: str) -> pl.DataFrame:
    return pl.DataFrame(schema=EVIDENCE_TABLE_SPECS[table_name].schema)


def conform_evidence_frame(table_name: str, frame: pl.DataFrame) -> pl.DataFrame:
    spec = EVIDENCE_TABLE_SPECS[table_name]
    missing = [name for name, _ in spec.columns if name not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} is missing columns: {', '.join(missing)}")
    return frame.select(
        [pl.col(name).cast(dtype, strict=True).alias(name) for name, dtype in spec.columns]
    )

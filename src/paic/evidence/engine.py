"""Build deterministic operational evidence, lineage, and incident timelines."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import polars as pl

from paic.analytics.io import load_analytics
from paic.analytics.validation import validate_analytics_directory
from paic.detection.io import load_detection
from paic.detection.validation import validate_detection_directory
from paic.evidence.config import EvidenceConfig, EvidenceRole, ServiceDefinition
from paic.evidence.schema import EVIDENCE_TABLE_ORDER, conform_evidence_frame, empty_evidence_frame
from paic.evidence.types import EvidenceBuildResult, EvidenceFrameMap
from paic.impact.io import load_impact
from paic.impact.validation import validate_impact_directory
from paic.simulator.io import file_sha256, load_dataset
from paic.simulator.validation import validate_dataset_directory


class EvidenceBuildError(RuntimeError):
    """Raised when source artifacts cannot produce valid operational evidence."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stable_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _record(
    *,
    evidence_type: str,
    source_name: str,
    source_ref: str,
    observed_at: datetime,
    valid_from: datetime,
    valid_to: datetime | None,
    service: str,
    component: str,
    region: str | None,
    severity: str,
    trust_level: str,
    role: EvidenceRole,
    title: str,
    summary: str,
    payload: object,
    incident_id: str,
) -> dict[str, object]:
    payload_json = _canonical_json(payload)
    identity = {
        "type": evidence_type,
        "source": source_name,
        "ref": source_ref,
        "observed_at": observed_at.isoformat(),
        "payload": payload_json,
    }
    return {
        "evidence_record_id": _stable_id("EVD", identity),
        "evidence_type": evidence_type,
        "source_name": source_name,
        "source_ref": source_ref,
        "observed_at": observed_at,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "service": service,
        "component": component,
        "region": region,
        "environment": "simulation",
        "severity": severity,
        "trust_level": trust_level,
        "evidence_role": role,
        "title": title,
        "summary": summary,
        "payload_json": payload_json,
        "content_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
        "incident_id": incident_id,
    }


def _status(error_rate: float, saturation: float) -> str:
    if error_rate >= 0.20 or saturation >= 0.95:
        return "critical"
    if error_rate >= 0.10 or saturation >= 0.85:
        return "degraded"
    if error_rate >= 0.04 or saturation >= 0.75:
        return "warning"
    return "healthy"


def _quantile(values: pl.Series, probability: float) -> float:
    if values.is_empty():
        return 0.0
    value = values.quantile(probability, interpolation="linear")
    return float(value or 0.0)


def _checkout_health(
    tables: dict[str, pl.DataFrame], service: ServiceDefinition
) -> list[dict[str, object]]:
    frame = tables["checkout_sessions"].with_columns(
        pl.col("started_at").dt.truncate("1h").alias("observed_at"),
        pl.when(pl.col("completed_at").is_not_null())
        .then((pl.col("completed_at") - pl.col("started_at")).dt.total_milliseconds())
        .otherwise(
            pl.when(pl.col("payment_started_at").is_not_null())
            .then((pl.col("payment_started_at") - pl.col("started_at")).dt.total_milliseconds())
            .otherwise(0)
        )
        .cast(pl.Float64)
        .alias("latency_ms"),
    )
    rows: list[dict[str, object]] = []
    for group in frame.partition_by(["observed_at", "region"], maintain_order=True):
        count = group.height
        errors = group.filter(pl.col("completed_at").is_null()).height
        error_rate = errors / count if count else 0.0
        latency = group.get_column("latency_ms")
        saturation = min(1.0, count / 80.0)
        rows.append(
            {
                "service": service.service,
                "component": service.component,
                "region": str(group.get_column("region")[0]),
                "observed_at": cast(datetime, group.get_column("observed_at")[0]),
                "window_minutes": 60,
                "request_count": count,
                "error_count": errors,
                "error_rate": error_rate,
                "p50_latency_ms": _quantile(latency, 0.50),
                "p95_latency_ms": _quantile(latency, 0.95),
                "saturation": saturation,
                "status": _status(error_rate, saturation),
            }
        )
    return rows


def _payment_health(
    tables: dict[str, pl.DataFrame], service: ServiceDefinition
) -> list[dict[str, object]]:
    frame = (
        tables["payment_attempts"]
        .join(tables["checkout_sessions"].select("session_id", "region"), on="session_id")
        .with_columns(pl.col("attempted_at").dt.truncate("1h").alias("observed_at"))
    )
    rows: list[dict[str, object]] = []
    for group in frame.partition_by(["observed_at", "region"], maintain_order=True):
        count = group.height
        errors = group.filter(~pl.col("approved")).height
        error_rate = errors / count if count else 0.0
        latency = group.get_column("latency_ms").cast(pl.Float64)
        saturation = min(1.0, count / 65.0)
        rows.append(
            {
                "service": service.service,
                "component": service.component,
                "region": str(group.get_column("region")[0]),
                "observed_at": cast(datetime, group.get_column("observed_at")[0]),
                "window_minutes": 60,
                "request_count": count,
                "error_count": errors,
                "error_rate": error_rate,
                "p50_latency_ms": _quantile(latency, 0.50),
                "p95_latency_ms": _quantile(latency, 0.95),
                "saturation": saturation,
                "status": _status(error_rate, saturation),
            }
        )
    return rows


def _pipeline_health(
    tables: dict[str, pl.DataFrame], service: ServiceDefinition
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in tables["pipeline_runs"].iter_rows(named=True):
        count = int(row["input_rows"])
        output = int(row["output_rows"])
        errors = max(count - output, 0)
        error_rate = errors / count if count else 0.0
        duration = cast(datetime, row["completed_at"]) - cast(datetime, row["started_at"])
        latency = duration.total_seconds() * 1000.0
        saturation = min(1.0, count / 100_000.0)
        rows.append(
            {
                "service": service.service,
                "component": service.component,
                "region": "global",
                "observed_at": row["started_at"],
                "window_minutes": max(1, round(duration.total_seconds() / 60.0)),
                "request_count": count,
                "error_count": errors,
                "error_rate": error_rate,
                "p50_latency_ms": latency,
                "p95_latency_ms": latency,
                "saturation": saturation,
                "status": _status(error_rate, saturation),
            }
        )
    return rows


def _fulfilment_health(
    tables: dict[str, pl.DataFrame], service: ServiceDefinition
) -> list[dict[str, object]]:
    frame = tables["shipments"].with_columns(
        pl.col("promised_delivery_at").dt.truncate("1d").alias("observed_at"),
        pl.when(pl.col("delivered_at").is_not_null())
        .then((pl.col("delivered_at") - pl.col("shipped_at")).dt.total_milliseconds())
        .otherwise(0)
        .cast(pl.Float64)
        .alias("latency_ms"),
    )
    rows: list[dict[str, object]] = []
    for group in frame.partition_by("observed_at", maintain_order=True):
        count = group.height
        errors = group.filter(pl.col("late")).height
        error_rate = errors / count if count else 0.0
        latency = group.get_column("latency_ms")
        saturation = min(1.0, count / 750.0)
        rows.append(
            {
                "service": service.service,
                "component": service.component,
                "region": "global",
                "observed_at": group.get_column("observed_at")[0],
                "window_minutes": 1440,
                "request_count": count,
                "error_count": errors,
                "error_rate": error_rate,
                "p50_latency_ms": _quantile(latency, 0.50),
                "p95_latency_ms": _quantile(latency, 0.95),
                "saturation": saturation,
                "status": _status(error_rate, saturation),
            }
        )
    return rows


def _seller_feed_health(
    tables: dict[str, pl.DataFrame], service: ServiceDefinition
) -> list[dict[str, object]]:
    frame = tables["seller_feed_runs"].with_columns(
        pl.col("started_at").dt.truncate("1d").alias("observed_at")
    )
    rows: list[dict[str, object]] = []
    for group in frame.partition_by("observed_at", maintain_order=True):
        received = int(group.get_column("records_received").sum())
        rejected = int(group.get_column("records_rejected").sum())
        error_rate = rejected / received if received else 0.0
        latency = group.get_column("latency_seconds").cast(pl.Float64) * 1000.0
        saturation = min(1.0, received / 1_000_000.0)
        rows.append(
            {
                "service": service.service,
                "component": service.component,
                "region": "global",
                "observed_at": group.get_column("observed_at")[0],
                "window_minutes": 1440,
                "request_count": received,
                "error_count": rejected,
                "error_rate": error_rate,
                "p50_latency_ms": _quantile(latency, 0.50),
                "p95_latency_ms": _quantile(latency, 0.95),
                "saturation": saturation,
                "status": _status(error_rate, saturation),
            }
        )
    return rows


_HEALTH_BUILDERS = {
    "checkout": _checkout_health,
    "payments": _payment_health,
    "pipeline": _pipeline_health,
    "fulfilment": _fulfilment_health,
    "seller_feed": _seller_feed_health,
}


def _build_health(
    tables: dict[str, pl.DataFrame], config: EvidenceConfig
) -> tuple[pl.DataFrame, list[dict[str, object]]]:
    health_rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    for service in config.services:
        if service.source_table not in tables:
            raise EvidenceBuildError(
                f"service {service.service} references missing table {service.source_table}"
            )
        rows = _HEALTH_BUILDERS[service.health_kind](tables, service)
        context_start = config.incident.started_at - timedelta(
            hours=config.incident.context_before_hours
        )
        context_end = config.incident.ended_at + timedelta(
            hours=config.incident.context_after_hours
        )
        rows = [
            row
            for row in rows
            if context_start <= cast(datetime, row["observed_at"]) <= context_end
        ]
        for row in rows:
            evidence_row = _record(
                evidence_type="service_health",
                source_name=service.source_table,
                source_ref=f"{service.service}:{cast(datetime, row['observed_at']).isoformat()}",
                observed_at=cast(datetime, row["observed_at"]),
                valid_from=cast(datetime, row["observed_at"]),
                valid_to=cast(datetime, row["observed_at"])
                + timedelta(minutes=cast(int, row["window_minutes"])),
                service=service.service,
                component=service.component,
                region=cast(str, row["region"]),
                severity=cast(str, row["status"]),
                trust_level="measured",
                role="context",
                title=f"{service.service} health window",
                summary=(
                    f"{row['request_count']} requests, {row['error_count']} errors, "
                    f"error rate {cast(float, row['error_rate']):.4f}."
                ),
                payload=row,
                incident_id=config.incident.incident_id,
            )
            row["health_observation_id"] = _stable_id("HLT", row)
            row["evidence_record_id"] = evidence_row["evidence_record_id"]
            health_rows.append(row)
            evidence.append(evidence_row)
    frame = (
        conform_evidence_frame(
            "service_health", pl.from_dicts(health_rows, infer_schema_length=None)
        ).sort(["observed_at", "service", "region"])
        if health_rows
        else empty_evidence_frame("service_health")
    )
    return frame, evidence


def _build_configured_evidence(
    config: EvidenceConfig,
) -> tuple[EvidenceFrameMap, list[dict[str, object]]]:
    evidence: list[dict[str, object]] = []
    change_rows: list[dict[str, object]] = []
    for change in config.config_changes:
        record = _record(
            evidence_type="change",
            source_name="configured_change_log",
            source_ref=change.change_id,
            observed_at=change.changed_at,
            valid_from=change.changed_at,
            valid_to=None,
            service=change.service,
            component=change.key,
            region=change.region_scope,
            severity="info",
            trust_level="authoritative",
            role=change.role,
            title=f"{change.change_type}: {change.key}",
            summary=change.description,
            payload=change.model_dump(mode="json"),
            incident_id=config.incident.incident_id,
        )
        change_rows.append(
            {
                **change.model_dump(mode="python"),
                "evidence_role": change.role,
                "evidence_record_id": record["evidence_record_id"],
            }
        )
        evidence.append(record)

    flag_rows: list[dict[str, object]] = []
    for flag in config.feature_flags:
        record = _record(
            evidence_type="feature_flag",
            source_name="feature_flag_history",
            source_ref=flag.transition_id,
            observed_at=flag.valid_from,
            valid_from=flag.valid_from,
            valid_to=flag.valid_to,
            service=flag.service,
            component=flag.flag_key,
            region=flag.region_scope,
            severity="info",
            trust_level="authoritative",
            role=flag.role,
            title=f"Feature flag {flag.flag_key}={flag.value}",
            summary=flag.description,
            payload=flag.model_dump(mode="json"),
            incident_id=config.incident.incident_id,
        )
        flag_rows.append(
            {
                "flag_state_id": flag.transition_id,
                "flag_key": flag.flag_key,
                "value": flag.value,
                "service": flag.service,
                "region_scope": flag.region_scope,
                "valid_from": flag.valid_from,
                "valid_to": flag.valid_to,
                "changed_by": flag.changed_by,
                "evidence_role": flag.role,
                "description": flag.description,
                "evidence_record_id": record["evidence_record_id"],
            }
        )
        evidence.append(record)

    runbook_rows: list[dict[str, object]] = []
    for runbook in config.runbooks:
        payload = runbook.model_dump(mode="json")
        record = _record(
            evidence_type="runbook",
            source_name="runbook_registry",
            source_ref=runbook.runbook_id,
            observed_at=config.incident.started_at,
            valid_from=config.incident.started_at,
            valid_to=None,
            service=runbook.service,
            component="runbook",
            region=None,
            severity="info",
            trust_level="curated",
            role="context",
            title=runbook.title,
            summary=runbook.trigger,
            payload=payload,
            incident_id=config.incident.incident_id,
        )
        runbook_rows.append(
            {
                "runbook_id": runbook.runbook_id,
                "title": runbook.title,
                "service": runbook.service,
                "trigger": runbook.trigger,
                "diagnostic_steps_json": _canonical_json(runbook.diagnostic_steps),
                "remediation_steps_json": _canonical_json(runbook.remediation_steps),
                "rollback_steps_json": _canonical_json(runbook.rollback_steps),
                "owner": runbook.owner,
                "version": runbook.version,
                "evidence_record_id": record["evidence_record_id"],
            }
        )
        evidence.append(record)

    incident_rows: list[dict[str, object]] = []
    for historical in config.historical_incidents:
        record = _record(
            evidence_type="historical_incident",
            source_name="incident_archive",
            source_ref=historical.historical_incident_id,
            observed_at=historical.ended_at,
            valid_from=historical.started_at,
            valid_to=historical.ended_at,
            service=historical.service,
            component="incident",
            region=None,
            severity="info",
            trust_level="curated",
            role="context",
            title=f"Historical incident: {historical.family}",
            summary=historical.root_cause,
            payload=historical.model_dump(mode="json"),
            incident_id=config.incident.incident_id,
        )
        incident_rows.append(
            {
                "historical_incident_id": historical.historical_incident_id,
                "family": historical.family,
                "service": historical.service,
                "started_at": historical.started_at,
                "ended_at": historical.ended_at,
                "root_cause": historical.root_cause,
                "symptoms_json": _canonical_json(historical.symptoms),
                "remediation": historical.remediation,
                "recovery_minutes": historical.recovery_minutes,
                "tags_json": _canonical_json(historical.tags),
                "evidence_record_id": record["evidence_record_id"],
            }
        )
        evidence.append(record)

    frames: EvidenceFrameMap = {
        "config_changes": conform_evidence_frame(
            "config_changes", pl.from_dicts(change_rows, infer_schema_length=None)
        ).sort("changed_at")
        if change_rows
        else empty_evidence_frame("config_changes"),
        "feature_flag_states": conform_evidence_frame(
            "feature_flag_states", pl.from_dicts(flag_rows, infer_schema_length=None)
        ).sort(["flag_key", "valid_from"])
        if flag_rows
        else empty_evidence_frame("feature_flag_states"),
        "runbooks": conform_evidence_frame(
            "runbooks", pl.from_dicts(runbook_rows, infer_schema_length=None)
        ).sort("runbook_id")
        if runbook_rows
        else empty_evidence_frame("runbooks"),
        "historical_incidents": conform_evidence_frame(
            "historical_incidents", pl.from_dicts(incident_rows, infer_schema_length=None)
        ).sort("started_at")
        if incident_rows
        else empty_evidence_frame("historical_incidents"),
    }
    return frames, evidence


def _build_deployment_evidence(
    tables: dict[str, pl.DataFrame], config: EvidenceConfig
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    window_start = config.incident.started_at - timedelta(
        hours=config.incident.context_before_hours
    )
    window_end = config.incident.ended_at + timedelta(hours=config.incident.context_after_hours)
    for row in tables["deployments"].iter_rows(named=True):
        role: EvidenceRole = "context"
        deployed_at = cast(datetime, row["deployed_at"])
        region_matches = row["region_scope"] in {"all", "global", config.incident.region}
        if (
            row["service"] == config.incident.primary_service
            and window_start <= deployed_at <= window_end
            and region_matches
            and str(row["status"]).lower() in {"applied", "succeeded", "success"}
        ):
            role = "supporting"
        records.append(
            _record(
                evidence_type="deployment",
                source_name="deployments",
                source_ref=str(row["deployment_id"]),
                observed_at=cast(datetime, row["deployed_at"]),
                valid_from=cast(datetime, row["deployed_at"]),
                valid_to=None,
                service=str(row["service"]),
                component=str(row["change_type"]),
                region=str(row["region_scope"]),
                severity="info",
                trust_level="authoritative",
                role=role,
                title=f"Deployment {row['version']} to {row['service']}",
                summary=(
                    f"Status {row['status']}; rollback available={row['rollback_available']}."
                ),
                payload=row,
                incident_id=config.incident.incident_id,
            )
        )
    return records


def _lineage_has_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node: 0 for node in nodes}
    for upstream, downstream in edges:
        adjacency[upstream].append(downstream)
        indegree[downstream] = indegree.get(downstream, 0) + 1
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for downstream in sorted(adjacency[node]):
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)
    return visited != len(indegree)


def _build_lineage(
    config: EvidenceConfig,
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, object]]]:
    evidence: list[dict[str, object]] = []
    node_rows: list[dict[str, object]] = []
    for node in config.lineage_nodes:
        record = _record(
            evidence_type="lineage_node",
            source_name="lineage_registry",
            source_ref=node.node_id,
            observed_at=config.incident.started_at,
            valid_from=config.incident.started_at,
            valid_to=None,
            service=node.system,
            component=node.node_type,
            region=None,
            severity="info",
            trust_level="curated",
            role="context",
            title=f"Lineage node: {node.name}",
            summary=node.description,
            payload=node.model_dump(mode="json"),
            incident_id=config.incident.incident_id,
        )
        node_rows.append(
            {**node.model_dump(mode="python"), "evidence_record_id": record["evidence_record_id"]}
        )
        evidence.append(record)
    edge_rows: list[dict[str, object]] = []
    for edge in config.lineage_edges:
        record = _record(
            evidence_type="lineage_edge",
            source_name="lineage_registry",
            source_ref=edge.edge_id,
            observed_at=edge.valid_from,
            valid_from=edge.valid_from,
            valid_to=None,
            service=edge.transform_name,
            component=edge.edge_type,
            region=None,
            severity="info",
            trust_level="curated",
            role="context",
            title=f"Lineage: {edge.upstream_node_id} -> {edge.downstream_node_id}",
            summary=f"{edge.edge_type} via {edge.transform_name}",
            payload=edge.model_dump(mode="json"),
            incident_id=config.incident.incident_id,
        )
        edge_rows.append(
            {**edge.model_dump(mode="python"), "evidence_record_id": record["evidence_record_id"]}
        )
        evidence.append(record)
    nodes = (
        conform_evidence_frame(
            "lineage_nodes", pl.from_dicts(node_rows, infer_schema_length=None)
        ).sort("node_id")
        if node_rows
        else empty_evidence_frame("lineage_nodes")
    )
    edges = (
        conform_evidence_frame(
            "lineage_edges", pl.from_dicts(edge_rows, infer_schema_length=None)
        ).sort("edge_id")
        if edge_rows
        else empty_evidence_frame("lineage_edges")
    )
    return nodes, edges, evidence


def _source_artifact_evidence(
    *,
    dataset_dir: str | Path,
    analytics_dir: str | Path | None,
    detection_dir: str | Path | None,
    impact_dir: str | Path | None,
    config: EvidenceConfig,
) -> tuple[list[dict[str, object]], str | None, str | None, str | None]:
    records: list[dict[str, object]] = []
    analytics_hash: str | None = None
    detection_hash: str | None = None
    impact_hash: str | None = None
    if analytics_dir is not None:
        analytics_report = validate_analytics_directory(analytics_dir, dataset_dir=dataset_dir)
        if not analytics_report.valid:
            raise EvidenceBuildError("source analytics artifact is invalid")
        loaded_analytics = load_analytics(analytics_dir)
        analytics_hash = file_sha256(Path(analytics_dir) / "manifest.json")
        records.append(
            _record(
                evidence_type="analytics_artifact",
                source_name="analytics",
                source_ref=loaded_analytics.manifest.analytics_id,
                observed_at=loaded_analytics.manifest.logical_end_at,
                valid_from=loaded_analytics.manifest.logical_start_at,
                valid_to=loaded_analytics.manifest.logical_end_at,
                service="analytics",
                component="metric_layer",
                region=None,
                severity="info",
                trust_level="derived",
                role="context",
                title="Validated analytical artifact",
                summary=f"{loaded_analytics.manifest.metric_count} configured metrics.",
                payload=loaded_analytics.manifest.model_dump(mode="json"),
                incident_id=config.incident.incident_id,
            )
        )
    if detection_dir is not None:
        if analytics_dir is None:
            raise EvidenceBuildError("detection source requires its analytics source")
        detection_report = validate_detection_directory(detection_dir, analytics_dir=analytics_dir)
        if not detection_report.valid:
            raise EvidenceBuildError("source detection artifact is invalid")
        loaded_detection = load_detection(detection_dir)
        detection_hash = file_sha256(Path(detection_dir) / "manifest.json")
        records.append(
            _record(
                evidence_type="detection_artifact",
                source_name="detection",
                source_ref=loaded_detection.manifest.detection_id,
                observed_at=loaded_detection.manifest.logical_end_at,
                valid_from=loaded_detection.manifest.logical_start_at,
                valid_to=loaded_detection.manifest.logical_end_at,
                service="detection",
                component="anomaly_engine",
                region=None,
                severity="warning" if loaded_detection.manifest.anomaly_event_count else "info",
                trust_level="derived",
                role="supporting" if loaded_detection.manifest.anomaly_event_count else "context",
                title="Validated anomaly-detection artifact",
                summary=f"{loaded_detection.manifest.anomaly_event_count} anomaly events.",
                payload=loaded_detection.manifest.model_dump(mode="json"),
                incident_id=config.incident.incident_id,
            )
        )
    if impact_dir is not None:
        impact_report = validate_impact_directory(impact_dir, dataset_dir=dataset_dir)
        if not impact_report.valid:
            raise EvidenceBuildError("source impact artifact is invalid")
        loaded_impact = load_impact(impact_dir)
        impact_hash = file_sha256(Path(impact_dir) / "manifest.json")
        records.append(
            _record(
                evidence_type="impact_artifact",
                source_name="impact",
                source_ref=loaded_impact.manifest.impact_id,
                observed_at=loaded_impact.manifest.logical_end_at,
                valid_from=loaded_impact.manifest.logical_start_at,
                valid_to=loaded_impact.manifest.logical_end_at,
                service="impact",
                component="customer_impact",
                region=None,
                severity="warning",
                trust_level="derived",
                role="context",
                title="Validated customer-impact artifact",
                summary=f"{loaded_impact.manifest.exposed_customer_count} exposed customers.",
                payload=loaded_impact.manifest.model_dump(mode="json"),
                incident_id=config.incident.incident_id,
            )
        )
    return records, analytics_hash, detection_hash, impact_hash


def _build_timeline(config: EvidenceConfig, evidence_records: pl.DataFrame) -> pl.DataFrame:
    lower = config.incident.started_at - timedelta(hours=config.incident.context_before_hours)
    upper = config.incident.ended_at + timedelta(hours=config.incident.context_after_hours)
    timeline: list[dict[str, object]] = [
        {
            "occurred_at": config.incident.started_at,
            "event_type": "incident_started",
            "source": "incident_context",
            "title": "Incident started",
            "detail": config.incident.family,
            "evidence_record_ids_json": "[]",
        },
        {
            "occurred_at": config.incident.ended_at,
            "event_type": "incident_ended",
            "source": "incident_context",
            "title": "Incident ended",
            "detail": config.incident.family,
            "evidence_record_ids_json": "[]",
        },
    ]
    selected = evidence_records.filter(
        (pl.col("observed_at") >= lower) & (pl.col("observed_at") <= upper)
    ).sort(["observed_at", "evidence_record_id"])
    for row in selected.iter_rows(named=True):
        timeline.append(
            {
                "occurred_at": row["observed_at"],
                "event_type": row["evidence_type"],
                "source": row["source_name"],
                "title": row["title"],
                "detail": row["summary"],
                "evidence_record_ids_json": _canonical_json([row["evidence_record_id"]]),
            }
        )
    timeline.sort(
        key=lambda item: (
            cast(datetime, item["occurred_at"]),
            str(item["event_type"]),
            str(item["title"]),
        )
    )
    rows: list[dict[str, object]] = []
    for sequence, item in enumerate(timeline, start=1):
        rows.append(
            {
                "timeline_event_id": _stable_id(
                    "TML",
                    {
                        "sequence": sequence,
                        "occurred_at": item["occurred_at"],
                        "title": item["title"],
                    },
                ),
                "incident_id": config.incident.incident_id,
                "sequence": sequence,
                **item,
            }
        )
    return conform_evidence_frame(
        "incident_timeline", pl.from_dicts(rows, infer_schema_length=None)
    ).sort("sequence")


def _quality_row(
    check_name: str,
    category: str,
    status: str,
    observed: float,
    expected: str,
    details: str,
    severity: str = "error",
) -> dict[str, object]:
    return {
        "check_name": check_name,
        "category": category,
        "severity": severity,
        "status": status,
        "observed_value": observed,
        "expected": expected,
        "details": details,
    }


def _build_quality(tables: EvidenceFrameMap, config: EvidenceConfig) -> pl.DataFrame:
    records = tables["evidence_records"]
    health = tables["service_health"]
    nodes = tables["lineage_nodes"]
    edges = tables["lineage_edges"]
    timeline = tables["incident_timeline"]
    rows: list[dict[str, object]] = []
    duplicate_records = int(records.select("evidence_record_id").is_duplicated().sum())
    rows.append(
        _quality_row(
            "evidence.unique_ids",
            "integrity",
            "pass" if duplicate_records == 0 else "fail",
            duplicate_records,
            "0 duplicate IDs",
            "Evidence record IDs must be unique.",
        )
    )
    invalid_hashes = (
        records.filter(
            pl.struct("payload_json", "content_sha256").map_elements(
                lambda item: (
                    hashlib.sha256(str(item["payload_json"]).encode()).hexdigest()
                    != item["content_sha256"]
                ),
                return_dtype=pl.Boolean,
            )
        ).height
        if not records.is_empty()
        else 0
    )
    rows.append(
        _quality_row(
            "evidence.content_hashes",
            "integrity",
            "pass" if invalid_hashes == 0 else "fail",
            invalid_hashes,
            "0 invalid hashes",
            "Every payload hash must reconstruct.",
        )
    )
    known_records = set(records.get_column("evidence_record_id").to_list())
    referenced: list[str] = []
    for name in (
        "config_changes",
        "feature_flag_states",
        "service_health",
        "lineage_nodes",
        "lineage_edges",
        "historical_incidents",
        "runbooks",
    ):
        if not tables[name].is_empty():
            referenced.extend(
                str(item) for item in tables[name].get_column("evidence_record_id").to_list()
            )
    missing_refs = sum(item not in known_records for item in referenced)
    rows.append(
        _quality_row(
            "evidence.references",
            "integrity",
            "pass" if missing_refs == 0 else "fail",
            missing_refs,
            "0 missing evidence references",
            "All domain tables must reference the evidence catalog.",
        )
    )
    health_invalid = (
        health.filter(
            (pl.col("request_count") < 0)
            | (pl.col("error_count") < 0)
            | (pl.col("error_count") > pl.col("request_count"))
            | (
                (
                    pl.col("error_rate")
                    - pl.col("error_count")
                    / pl.when(pl.col("request_count") > 0)
                    .then(pl.col("request_count"))
                    .otherwise(1)
                ).abs()
                > 1e-12
            )
            | (pl.col("saturation") < 0)
            | (pl.col("saturation") > 1)
        ).height
        if not health.is_empty()
        else 0
    )
    rows.append(
        _quality_row(
            "health.arithmetic",
            "health",
            "pass" if health_invalid == 0 else "fail",
            health_invalid,
            "0 invalid health rows",
            "Health rates and bounds must reconcile.",
        )
    )
    node_ids = set(str(item) for item in nodes.get_column("node_id").to_list())
    missing_lineage = 0
    pairs: list[tuple[str, str]] = []
    for row in edges.iter_rows(named=True):
        upstream = str(row["upstream_node_id"])
        downstream = str(row["downstream_node_id"])
        pairs.append((upstream, downstream))
        if upstream not in node_ids or downstream not in node_ids:
            missing_lineage += 1
    cyclic = _lineage_has_cycle(node_ids, pairs) if node_ids else False
    rows.append(
        _quality_row(
            "lineage.references",
            "lineage",
            "pass" if missing_lineage == 0 else "fail",
            missing_lineage,
            "0 missing node references",
            "Every lineage edge must reference known nodes.",
        )
    )
    rows.append(
        _quality_row(
            "lineage.acyclic",
            "lineage",
            "pass" if not cyclic else "fail",
            float(cyclic),
            "acyclic directed graph",
            "Lineage must not contain cycles.",
        )
    )
    timeline_sequences = timeline.get_column("sequence").to_list()
    ordered = timeline_sequences == list(range(1, timeline.height + 1))
    chronological = timeline.get_column("occurred_at").to_list() == sorted(
        timeline.get_column("occurred_at").to_list()
    )
    rows.append(
        _quality_row(
            "timeline.ordering",
            "timeline",
            "pass" if ordered and chronological else "fail",
            float(not (ordered and chronological)),
            "contiguous chronological sequence",
            "Incident timeline must be stable and ordered.",
        )
    )
    service_coverage = set(config_service.service for config_service in config.services).difference(
        set(health.get_column("service").unique().to_list())
    )
    rows.append(
        _quality_row(
            "health.service_coverage",
            "health",
            "pass" if not service_coverage else "fail",
            len(service_coverage),
            "all configured services represented",
            f"Missing services: {sorted(service_coverage)}",
        )
    )
    return conform_evidence_frame(
        "evidence_quality_results", pl.from_dicts(rows, infer_schema_length=None)
    ).sort(["category", "check_name"])


def evidence_quality_error_count(frame: pl.DataFrame) -> int:
    return (
        frame.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).height
        if not frame.is_empty()
        else 0
    )


def build_evidence(
    dataset_dir: str | Path,
    config: EvidenceConfig,
    *,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
) -> EvidenceBuildResult:
    """Build a source-bound operational evidence artifact."""

    dataset_report = validate_dataset_directory(dataset_dir)
    if not dataset_report.valid:
        raise EvidenceBuildError("source dataset is invalid")
    dataset_manifest, source_tables = load_dataset(dataset_dir)
    source_manifest_hash = file_sha256(Path(dataset_dir) / "manifest.json")
    source_records, analytics_hash, detection_hash, impact_hash = _source_artifact_evidence(
        dataset_dir=dataset_dir,
        analytics_dir=analytics_dir,
        detection_dir=detection_dir,
        impact_dir=impact_dir,
        config=config,
    )
    health, health_evidence = _build_health(source_tables, config)
    configured_frames, configured_evidence = _build_configured_evidence(config)
    nodes, edges, lineage_evidence = _build_lineage(config)
    deployment_evidence = _build_deployment_evidence(source_tables, config)
    evidence_rows = [
        *health_evidence,
        *configured_evidence,
        *lineage_evidence,
        *deployment_evidence,
        *source_records,
    ]
    evidence_records = conform_evidence_frame(
        "evidence_records", pl.from_dicts(evidence_rows, infer_schema_length=None)
    ).sort(["observed_at", "evidence_record_id"])
    tables: EvidenceFrameMap = {
        "evidence_records": evidence_records,
        "config_changes": configured_frames["config_changes"],
        "feature_flag_states": configured_frames["feature_flag_states"],
        "service_health": health,
        "lineage_nodes": nodes,
        "lineage_edges": edges,
        "historical_incidents": configured_frames["historical_incidents"],
        "runbooks": configured_frames["runbooks"],
        "incident_timeline": _build_timeline(config, evidence_records),
        "evidence_quality_results": empty_evidence_frame("evidence_quality_results"),
    }
    tables["evidence_quality_results"] = _build_quality(tables, config)
    if evidence_quality_error_count(tables["evidence_quality_results"]):
        raise EvidenceBuildError("operational evidence quality checks failed")
    if set(tables) != set(EVIDENCE_TABLE_ORDER):
        raise EvidenceBuildError("evidence table set is incomplete")
    return EvidenceBuildResult(
        config=config,
        source_dataset_manifest=dataset_manifest,
        source_dataset_manifest_sha256=source_manifest_hash,
        source_analytics_manifest_sha256=analytics_hash,
        source_detection_manifest_sha256=detection_hash,
        source_impact_manifest_sha256=impact_hash,
        tables=tables,
    )

"""Validation for exported operational evidence artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from paic.artifacts.lease import artifact_reader
from paic.evidence.config import EvidenceConfig
from paic.evidence.engine import (
    EvidenceBuildError,
    _canonical_json,
    _lineage_has_cycle,
    _stable_id,
    build_evidence,
    evidence_quality_error_count,
)
from paic.evidence.io import EvidenceIOError, load_evidence
from paic.evidence.schema import EVIDENCE_TABLE_ORDER, EVIDENCE_TABLE_SPECS
from paic.evidence.types import LoadedEvidence
from paic.simulator.io import file_sha256


@dataclass(frozen=True)
class EvidenceValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class EvidenceValidationReport:
    valid: bool
    issues: list[EvidenceValidationIssue]
    summary: dict[str, object]


def _source_hash(path: str | Path | None) -> str | None:
    if path is None:
        return None
    manifest = Path(path) / "manifest.json"
    return file_sha256(manifest) if manifest.is_file() else None


def _semantic_issues(
    loaded: LoadedEvidence, config: EvidenceConfig
) -> list[EvidenceValidationIssue]:
    """Validate relationships that remain meaningful without source directories."""
    tables = loaded.tables
    issues: list[EvidenceValidationIssue] = []
    records = tables["evidence_records"]
    known = set(records.get_column("evidence_record_id").to_list())
    for row in records.iter_rows(named=True):
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            issues.append(EvidenceValidationIssue("evidence.payload", "payload JSON is invalid"))
            continue
        canonical = _canonical_json(payload)
        identity = {
            "type": row["evidence_type"],
            "source": row["source_name"],
            "ref": row["source_ref"],
            "observed_at": row["observed_at"].isoformat(),
            "payload": canonical,
        }
        if str(row["payload_json"]) != canonical or str(row["content_sha256"]) != file_sha256_text(
            canonical
        ):
            issues.append(
                EvidenceValidationIssue("evidence.content", "canonical payload or hash drift")
            )
        if str(row["evidence_record_id"]) != _stable_id("EVD", identity):
            issues.append(
                EvidenceValidationIssue("evidence.id", "evidence record ID does not reconstruct")
            )
    for name in (
        "config_changes",
        "feature_flag_states",
        "service_health",
        "lineage_nodes",
        "lineage_edges",
        "historical_incidents",
        "runbooks",
    ):
        missing = set(tables[name].get_column("evidence_record_id").to_list()).difference(known)
        if missing:
            issues.append(
                EvidenceValidationIssue(
                    "evidence.references", f"{name} has broken evidence references"
                )
            )
    nodes = tables["lineage_nodes"]
    edges = tables["lineage_edges"]
    node_ids = set(nodes.get_column("node_id").to_list())
    pairs = [
        (str(row["upstream_node_id"]), str(row["downstream_node_id"]))
        for row in edges.iter_rows(named=True)
    ]
    if any(
        upstream == downstream or upstream not in node_ids or downstream not in node_ids
        for upstream, downstream in pairs
    ):
        issues.append(EvidenceValidationIssue("lineage.references", "lineage edges are invalid"))
    if len(set(pairs)) != len(pairs) or _lineage_has_cycle(node_ids, pairs):
        issues.append(
            EvidenceValidationIssue(
                "lineage.dag", "lineage must be an acyclic graph without duplicate edges"
            )
        )
    degree = {node: 0 for node in node_ids}
    for upstream, downstream in pairs:
        degree[upstream] = degree.get(upstream, 0) + 1
        degree[downstream] = degree.get(downstream, 0) + 1
    if node_ids and any(value == 0 for value in degree.values()):
        issues.append(EvidenceValidationIssue("lineage.orphans", "lineage contains orphan nodes"))
    source_ids = set(nodes.filter(pl.col("node_type") == "source").get_column("node_id").to_list())
    metric_ids = set(nodes.filter(pl.col("node_type") == "metric").get_column("node_id").to_list())
    reachable = set(source_ids)
    while True:
        expanded = reachable | {
            downstream for upstream, downstream in pairs if upstream in reachable
        }
        if expanded == reachable:
            break
        reachable = expanded
    if metric_ids and not metric_ids.issubset(reachable):
        issues.append(
            EvidenceValidationIssue(
                "lineage.reachability", "metrics are not reachable from sources"
            )
        )
    timeline = tables["incident_timeline"]
    expected = list(range(1, timeline.height + 1))
    if timeline.get_column("sequence").to_list() != expected or timeline.get_column(
        "occurred_at"
    ).to_list() != sorted(timeline.get_column("occurred_at").to_list()):
        issues.append(
            EvidenceValidationIssue("timeline.ordering", "timeline sequence or order is invalid")
        )
    for row in timeline.iter_rows(named=True):
        try:
            references = json.loads(str(row["evidence_record_ids_json"]))
        except json.JSONDecodeError:
            references = []
            issues.append(
                EvidenceValidationIssue(
                    "timeline.references", "timeline references are invalid JSON"
                )
            )
        if not isinstance(references, list) or not set(references).issubset(known):
            issues.append(
                EvidenceValidationIssue(
                    "timeline.references", "timeline has broken evidence references"
                )
            )
        expected_id = _stable_id(
            "TML",
            {"sequence": row["sequence"], "occurred_at": row["occurred_at"], "title": row["title"]},
        )
        if str(row["timeline_event_id"]) != expected_id:
            issues.append(
                EvidenceValidationIssue("timeline.id", "timeline event ID does not reconstruct")
            )
    return issues


def file_sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


@artifact_reader
def validate_evidence_directory(
    evidence_dir: str | Path,
    *,
    dataset_dir: str | Path | None = None,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
) -> EvidenceValidationReport:
    root = Path(evidence_dir)
    issues: list[EvidenceValidationIssue] = []
    try:
        loaded = load_evidence(root)
    except EvidenceIOError as exc:
        return EvidenceValidationReport(
            False, [EvidenceValidationIssue("evidence.load", str(exc))], {}
        )
    manifest = loaded.manifest
    if (
        manifest.source_detection_manifest_sha256 is not None
        and manifest.source_analytics_manifest_sha256 is None
    ):
        issues.append(
            EvidenceValidationIssue(
                "source.detection_binding",
                "detection source requires a corresponding analytics source",
            )
        )
    if detection_dir is not None and analytics_dir is None:
        issues.append(
            EvidenceValidationIssue(
                "source.detection_binding",
                "detection validation requires --analytics-dir",
            )
        )
    table_map = {item.name: item for item in manifest.tables}
    if set(table_map) != set(EVIDENCE_TABLE_ORDER):
        issues.append(
            EvidenceValidationIssue("manifest.tables", "evidence table set is incomplete")
        )
    for name in EVIDENCE_TABLE_ORDER:
        if name not in table_map or name not in loaded.tables:
            continue
        meta = table_map[name]
        frame = loaded.tables[name]
        path = root / meta.relative_path
        spec = EVIDENCE_TABLE_SPECS[name]
        if file_sha256(path) != meta.sha256:
            issues.append(EvidenceValidationIssue("evidence.hash", f"hash mismatch for {name}"))
        if path.stat().st_size != meta.byte_size or frame.height != meta.row_count:
            issues.append(
                EvidenceValidationIssue("evidence.metadata", f"metadata mismatch for {name}")
            )
        if frame.schema != spec.schema:
            issues.append(EvidenceValidationIssue("evidence.schema", f"schema mismatch for {name}"))
        if spec.primary_key and frame.select(spec.primary_key).is_duplicated().sum() > 0:
            issues.append(
                EvidenceValidationIssue("evidence.primary_key", f"duplicate key for {name}")
            )
    config_path = root / "evidence.config.resolved.json"
    try:
        config = EvidenceConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        config = None
        issues.append(EvidenceValidationIssue("evidence.config", str(exc)))
    if file_sha256(config_path) != manifest.evidence_config_sha256:
        issues.append(
            EvidenceValidationIssue("evidence.config_hash", "evidence config hash mismatch")
        )
    if config is not None:
        issues.extend(_semantic_issues(loaded, config))
    manifest_path = root / "manifest.json"
    marker = root / "_SUCCESS"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != file_sha256(
        manifest_path
    ):
        issues.append(EvidenceValidationIssue("evidence.success_marker", "success marker mismatch"))
    source_pairs = (
        ("source.dataset", dataset_dir, manifest.source_dataset_manifest_sha256),
        ("source.analytics", analytics_dir, manifest.source_analytics_manifest_sha256),
        ("source.detection", detection_dir, manifest.source_detection_manifest_sha256),
        ("source.impact", impact_dir, manifest.source_impact_manifest_sha256),
    )
    for code, source, source_expected in source_pairs:
        actual = _source_hash(source)
        if source_expected is not None and actual != source_expected:
            issues.append(EvidenceValidationIssue(code, f"{code} does not match manifest"))
    if dataset_dir is not None and config is not None:
        try:
            rebuilt = build_evidence(
                dataset_dir,
                config,
                analytics_dir=analytics_dir,
                detection_dir=detection_dir,
                impact_dir=impact_dir,
            )
        except EvidenceBuildError as exc:
            issues.append(EvidenceValidationIssue("evidence.recompute", str(exc)))
        else:
            for name in EVIDENCE_TABLE_ORDER:
                actual = loaded.tables.get(name)
                if actual is None or not actual.equals(rebuilt.tables[name]):
                    issues.append(
                        EvidenceValidationIssue(
                            "evidence.recompute",
                            f"{name} does not match deterministic source reconstruction",
                        )
                    )
    quality = loaded.tables.get("evidence_quality_results", pl.DataFrame())
    quality_errors = evidence_quality_error_count(quality)
    if quality_errors != manifest.quality_error_count or quality_errors:
        issues.append(
            EvidenceValidationIssue("evidence.quality", "evidence quality contains errors")
        )
    summary: dict[str, object] = {
        "evidence_id": manifest.evidence_id,
        "incident_id": manifest.incident_id,
        "evidence_records": manifest.evidence_record_count,
        "timeline_events": manifest.timeline_event_count,
        "lineage_nodes": manifest.lineage_node_count,
        "lineage_edges": manifest.lineage_edge_count,
        "quality_errors": quality_errors,
    }
    return EvidenceValidationReport(not issues, issues, summary)


def evidence_report_to_json(report: EvidenceValidationReport) -> str:
    return json.dumps(
        {
            "valid": report.valid,
            "issues": [asdict(item) for item in report.issues],
            "summary": report.summary,
        },
        indent=2,
        sort_keys=True,
    )

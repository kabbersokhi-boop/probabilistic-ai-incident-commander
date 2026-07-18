from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from paic.evidence.config import EvidenceConfig, ServiceDefinition
from paic.evidence.engine import (
    EvidenceBuildError,
    _fulfilment_health,
    _lineage_has_cycle,
    _pipeline_health,
    _seller_feed_health,
    build_evidence,
)
from paic.evidence.types import EvidenceBuildResult
from paic.simulator.io import load_dataset


def test_evidence_build_has_complete_reconciled_tables(
    evidence_smoke_result: EvidenceBuildResult,
) -> None:
    assert set(evidence_smoke_result.tables) == {
        "evidence_records",
        "config_changes",
        "feature_flag_states",
        "service_health",
        "lineage_nodes",
        "lineage_edges",
        "incident_timeline",
        "historical_incidents",
        "runbooks",
        "evidence_quality_results",
    }
    records = evidence_smoke_result.tables["evidence_records"]
    assert records.height > 100
    assert records.filter(pl.col("evidence_role") == "supporting").height >= 2
    assert records.filter(pl.col("evidence_role") == "contradictory").height >= 1
    assert (
        evidence_smoke_result.tables["evidence_quality_results"]
        .filter(pl.col("status") == "fail")
        .is_empty()
    )


def test_payload_hashes_and_references_reconstruct(
    evidence_smoke_result: EvidenceBuildResult,
) -> None:
    records = evidence_smoke_result.tables["evidence_records"]
    known = set(records.get_column("evidence_record_id").to_list())
    for row in records.iter_rows(named=True):
        assert (
            hashlib.sha256(str(row["payload_json"]).encode()).hexdigest() == row["content_sha256"]
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
        assert (
            set(evidence_smoke_result.tables[name].get_column("evidence_record_id").to_list())
            <= known
        )


def test_lineage_and_timeline_are_stable(evidence_smoke_result: EvidenceBuildResult) -> None:
    nodes = evidence_smoke_result.tables["lineage_nodes"]
    edges = evidence_smoke_result.tables["lineage_edges"]
    assert not _lineage_has_cycle(
        set(nodes.get_column("node_id").to_list()),
        [
            (str(row["upstream_node_id"]), str(row["downstream_node_id"]))
            for row in edges.iter_rows(named=True)
        ],
    )
    timeline = evidence_smoke_result.tables["incident_timeline"]
    assert timeline.get_column("sequence").to_list() == list(range(1, timeline.height + 1))
    assert timeline.get_column("occurred_at").to_list() == sorted(
        timeline.get_column("occurred_at").to_list()
    )
    for raw in timeline.get_column("evidence_record_ids_json"):
        assert isinstance(json.loads(raw), list)


def test_health_arithmetic_reconciles(evidence_smoke_result: EvidenceBuildResult) -> None:
    health = evidence_smoke_result.tables["service_health"]
    invalid = health.filter(
        (pl.col("error_count") > pl.col("request_count"))
        | (
            (
                pl.col("error_rate")
                - pl.col("error_count")
                / pl.when(pl.col("request_count") > 0).then(pl.col("request_count")).otherwise(1)
            ).abs()
            > 1e-12
        )
    )
    assert invalid.is_empty()


def test_all_health_builders_produce_bounded_results(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_config: EvidenceConfig,
) -> None:
    _, tables = load_dataset(impact_smoke_dataset_dir)
    definitions = {
        "pipeline": ServiceDefinition(
            service="data-platform",
            component="pipeline",
            owner="data",
            source_table="pipeline_runs",
            health_kind="pipeline",
        ),
        "fulfilment": ServiceDefinition(
            service="fulfilment-service",
            component="delivery",
            owner="operations",
            source_table="shipments",
            health_kind="fulfilment",
        ),
        "seller": ServiceDefinition(
            service="seller-feed-service",
            component="feed-ingestion",
            owner="marketplace",
            source_table="seller_feed_runs",
            health_kind="seller_feed",
        ),
    }
    rows = [
        *_pipeline_health(tables, definitions["pipeline"]),
        *_fulfilment_health(tables, definitions["fulfilment"]),
        *_seller_feed_health(tables, definitions["seller"]),
    ]
    assert rows
    assert all(0.0 <= float(cast(float | int, row["error_rate"])) <= 1.0 for row in rows)
    assert all(0.0 <= float(cast(float | int, row["saturation"])) <= 1.0 for row in rows)


def test_optional_source_artifacts_are_bound(
    tmp_path: Path,
    impact_smoke_dataset_dir: Path,
    analytics_smoke_config: object,
    detection_smoke_config: object,
    impact_smoke_dir: Path,
    evidence_smoke_config: EvidenceConfig,
) -> None:
    from paic.analytics.config import AnalyticsConfig
    from paic.analytics.engine import build_analytics
    from paic.analytics.io import export_analytics
    from paic.detection.config import DetectionConfig
    from paic.detection.engine import build_detection
    from paic.detection.io import export_detection

    assert isinstance(analytics_smoke_config, AnalyticsConfig)
    assert isinstance(detection_smoke_config, DetectionConfig)
    analytics_dir = tmp_path / "analytics"
    export_analytics(
        build_analytics(impact_smoke_dataset_dir, analytics_smoke_config), analytics_dir
    )
    detection_dir = tmp_path / "detection"
    export_detection(build_detection(analytics_dir, detection_smoke_config), detection_dir)
    result = build_evidence(
        impact_smoke_dataset_dir,
        evidence_smoke_config,
        analytics_dir=analytics_dir,
        detection_dir=detection_dir,
        impact_dir=impact_smoke_dir,
    )
    assert result.source_analytics_manifest_sha256
    assert result.source_detection_manifest_sha256
    assert result.source_impact_manifest_sha256
    types = set(result.tables["evidence_records"].get_column("evidence_type").to_list())
    assert {"analytics_artifact", "detection_artifact", "impact_artifact"} <= types


def test_rejects_optional_artifact_from_a_different_dataset(
    analytics_smoke_dir: Path,
    evidence_smoke_config: EvidenceConfig,
    impact_smoke_dataset_dir: Path,
) -> None:
    with pytest.raises(EvidenceBuildError, match="source analytics artifact is invalid"):
        build_evidence(
            impact_smoke_dataset_dir,
            evidence_smoke_config,
            analytics_dir=analytics_smoke_dir,
        )


def test_cycle_detection_and_missing_source_table(
    evidence_smoke_config: EvidenceConfig,
    impact_smoke_dataset_dir: Path,
) -> None:
    assert _lineage_has_cycle({"a", "b"}, [("a", "b"), ("b", "a")])
    bad_service = evidence_smoke_config.services[0].model_copy(
        update={"source_table": "missing_table"}
    )
    bad = evidence_smoke_config.model_copy(update={"services": [bad_service]})
    with pytest.raises(EvidenceBuildError, match="missing table"):
        build_evidence(impact_smoke_dataset_dir, bad)

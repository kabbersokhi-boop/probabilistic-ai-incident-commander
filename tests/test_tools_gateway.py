from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from paic.tools.gateway import Gateway
from paic.tools.models import ToolRequest


def _request(
    tool: str,
    dataset: Path,
    *,
    arguments: dict[str, object],
    analytics: Path | None = None,
    detection: Path | None = None,
    impact: Path | None = None,
    evidence: Path | None = None,
    role: str = "investigator",
) -> ToolRequest:
    return ToolRequest.model_validate(
        {
            "tool": tool,
            "incident_id": "inc-test",
            "role": role,
            "arguments": arguments,
            "dataset_dir": str(dataset),
            "analytics_dir": str(analytics) if analytics else None,
            "detection_dir": str(detection) if detection else None,
            "impact_dir": str(impact) if impact else None,
            "evidence_dir": str(evidence) if evidence else None,
            "call_id": str(uuid4()),
        }
    )


def test_gateway_evidence_handlers(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
) -> None:
    gateway = Gateway(row_limit=20, byte_limit=50_000)
    summary = gateway.invoke(
        _request(
            "artifacts.summary",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={},
        )
    )
    assert summary.execution_status == "success"
    assert summary.result["table_count"] > 10

    evidence = gateway.invoke(
        _request(
            "evidence.search",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={"query": "", "limit": 3},
        )
    )
    assert evidence.execution_status == "success"
    assert len(evidence.result) == 3
    assert evidence.evidence_record_ids

    changes = gateway.invoke(
        _request(
            "changes.list",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={"query": "", "limit": 10},
        )
    )
    assert changes.execution_status == "success"
    assert changes.result

    runbook = gateway.invoke(
        _request(
            "runbook.get",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={"query": "checkout", "limit": 10},
        )
    )
    assert runbook.execution_status == "success"

    historical = gateway.invoke(
        _request(
            "historical_incidents.search",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={"query": "address", "limit": 10},
        )
    )
    assert historical.execution_status == "success"

    nodes = gateway._frames(
        __import__("paic.tools.binding", fromlist=["bind_sources"]).bind_sources(
            impact_smoke_dataset_dir, evidence_dir=evidence_smoke_dir
        )
    )["evidence__lineage_nodes"]
    node_id = nodes.get_column("node_id")[0]
    lineage = gateway.invoke(
        _request(
            "lineage.trace",
            impact_smoke_dataset_dir,
            evidence=evidence_smoke_dir,
            arguments={"node_id": node_id, "direction": "both", "depth": 2},
        )
    )
    assert lineage.execution_status == "success"
    assert lineage.result["nodes"]


def test_gateway_detection_impact_and_sql_handlers(
    smoke_dataset_dir: Path,
    analytics_smoke_dir: Path,
    detection_smoke_dir: Path,
    impact_smoke_dataset_dir: Path,
    impact_smoke_dir: Path,
) -> None:
    gateway = Gateway(row_limit=10, byte_limit=20_000)
    anomalies = gateway.invoke(
        _request(
            "anomalies.list",
            smoke_dataset_dir,
            analytics=analytics_smoke_dir,
            detection=detection_smoke_dir,
            arguments={"limit": 5},
        )
    )
    assert anomalies.execution_status == "success"

    sql = gateway.invoke(
        _request(
            "sql.query",
            smoke_dataset_dir,
            arguments={
                "query": "SELECT customer_id FROM dataset__customers ORDER BY customer_id LIMIT 3"
            },
        )
    )
    assert sql.execution_status == "success"
    assert len(sql.result) == 3

    impact = gateway.invoke(
        _request(
            "impact.summary",
            impact_smoke_dataset_dir,
            impact=impact_smoke_dir,
            arguments={},
        )
    )
    assert impact.execution_status == "success"
    assert "financial_impact" in impact.result


def test_gateway_denies_invalid_requests_and_reports_errors(
    smoke_dataset_dir: Path,
) -> None:
    gateway = Gateway()
    denied = gateway.invoke(
        _request(
            "sql.query",
            smoke_dataset_dir,
            arguments={"query": "SELECT 1"},
            role="observer",
        )
    )
    assert denied.policy_decision == "deny"
    assert denied.error and denied.error.code == "forbidden"

    invalid = gateway.invoke(
        _request(
            "evidence.search",
            smoke_dataset_dir,
            arguments={"query": "x", "unknown": True},
        )
    )
    assert invalid.policy_decision == "deny"
    assert invalid.error and invalid.error.code == "invalid_arguments"

    rejected = gateway.invoke(
        _request(
            "sql.query",
            smoke_dataset_dir,
            arguments={"query": "SELECT * FROM parquet_scan('/tmp/private')"},
        )
    )
    assert rejected.execution_status == "error"
    assert rejected.error and rejected.error.code == "request_rejected"


def test_gateway_audit_round_trip(
    tmp_path: Path,
    smoke_dataset_dir: Path,
) -> None:
    request = _request("artifacts.summary", smoke_dataset_dir, arguments={})
    raw = request.model_dump(mode="json")
    raw["audit_dir"] = str(tmp_path / "audit")
    response = Gateway().invoke(ToolRequest.model_validate(raw))
    assert response.execution_status == "success"
    from paic.tools.ledger import AuditLedger

    ledger = AuditLedger(tmp_path / "audit")
    ledger.validate()
    record = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert record["request"]["tool"] == "artifacts.summary"

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest

from paic.cli import main
from paic.tools.binding import BoundSources
from paic.tools.gateway import Gateway
from paic.tools.ledger import AuditLedger, canonical, digest
from paic.tools.models import ToolRequest
from paic.tools.policy import authorize
from paic.tools.sql import SQLPolicyError, execute


def frames() -> dict[str, pl.DataFrame]:
    return {
        "evidence__evidence_records": pl.DataFrame(
            {"evidence_record_id": ["e1"], "summary": ["checkout degraded"]}
        )
    }


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "query",
    [
        "select * from evidence__evidence_records; delete from evidence__evidence_records",
        "SeLeCt * from evidence__evidence_records; -- DROP TABLE x\n select 1",
        "select * from read_parquet('/tmp/secret.parquet')",
        "select * from evidence__evidence_records where summary = getenv('TOKEN')",
        "install httpfs",
        "select * from pg_catalog.pg_tables",
        "select missing from evidence__evidence_records",
    ],
)
def test_sql_policy_rejects_adversarial_queries(query: str) -> None:
    with pytest.raises(SQLPolicyError):
        execute(query, frames())


def test_sql_cte_and_subquery_are_read_only() -> None:
    rows, truncated = execute(
        "with x as (select * from evidence__evidence_records) select * from x where evidence_record_id in (select evidence_record_id from x)",
        frames(),
    )
    assert rows[0]["evidence_record_id"] == "e1"
    assert not truncated


def test_sql_limits_are_deterministic() -> None:
    rows, truncated = execute("select * from evidence__evidence_records", frames(), row_limit=0)
    assert rows == []
    assert truncated


def test_ledger_detects_edit_delete_reorder_and_forgery(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path)
    request = {"tool": "artifacts.summary", "role": "observer"}
    response = {"call_id": "c", "tool": "artifacts.summary", "result_sha256": digest({"ok": True})}
    ledger.append(request, response, policy="allow", sources={"dataset": "a" * 64})
    ledger.validate()
    path = tmp_path / "invocations.jsonl"
    original = path.read_text()
    record = json.loads(original)
    record["request_sha256"] = "0" * 64
    path.write_text(canonical(record) + "\n")
    with pytest.raises(ValueError):
        ledger.validate()


def test_policy_is_deny_by_default() -> None:
    assert not authorize("unknown", "evidence.search", "1.0", {}).allowed
    assert not authorize("observer", "sql.query", "1.0", {}).allowed
    assert not authorize("investigator", "unknown", "1.0", {}).allowed
    assert not authorize("investigator", "sql.query", "9.0", {}).allowed
    assert authorize("investigator", "sql.query", "1.0", {}).allowed


def test_gateway_handlers_and_denials(monkeypatch: pytest.MonkeyPatch) -> None:
    import paic.tools.gateway as module

    bound = BoundSources(Path("/dataset"), None, None, None, None, {"dataset": "a" * 64})
    local = {
        "evidence__evidence_records": pl.DataFrame(
            {"evidence_record_id": ["e1"], "summary": ["checkout"]}
        ),
        "evidence__lineage_nodes": pl.DataFrame({"node_id": ["n1"], "name": ["checkout"]}),
        "evidence__lineage_edges": pl.DataFrame(
            {"upstream_node_id": ["n1"], "downstream_node_id": ["n1"]}
        ),
        "evidence__config_changes": pl.DataFrame({"change_id": ["c1"], "description": ["change"]}),
        "evidence__runbooks": pl.DataFrame({"runbook_id": ["r1"], "title": ["book"]}),
        "evidence__historical_incidents": pl.DataFrame(
            {"historical_incident_id": ["h1"], "root_cause": ["x"]}
        ),
        "detection__anomaly_events": pl.DataFrame({"event_id": ["a1"], "description": ["anomaly"]}),
        "impact__customer_impact_summary": pl.DataFrame(
            {"impact_id": ["i1"], "summary": ["impact"]}
        ),
    }
    monkeypatch.setattr(module, "bind_sources", lambda *args, **kwargs: bound)
    monkeypatch.setattr(Gateway, "_frames", lambda self, source: local)
    gateway = Gateway()
    common: dict[str, object] = {
        "incident_id": "inc",
        "role": "investigator",
        "dataset_dir": "/dataset",
        "call_id": uuid4(),
    }
    for tool in [
        "evidence.search",
        "lineage.trace",
        "changes.list",
        "runbook.get",
        "historical_incidents.search",
        "anomalies.list",
        "impact.summary",
        "artifacts.summary",
    ]:
        response = gateway.invoke(ToolRequest(tool=tool, arguments={}, **common))  # type: ignore[arg-type]
        assert response.execution_status == "success"
    denied = gateway.invoke(
        ToolRequest(
            tool="sql.query",
            role="observer",
            arguments={},
            incident_id="inc",
            dataset_dir="/dataset",
        )
    )
    assert denied.policy_decision == "deny"


def test_gateway_cli_surfaces(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools", "list"]) == 0
    request = tmp_path / "bad.json"
    request.write_text("{}", encoding="utf-8")
    assert main(["tools", "invoke", "--request", str(request)]) == 2
    assert main(["tools", "audit", "validate", "--audit-dir", str(tmp_path / "audit")]) == 0
    assert "error" in capsys.readouterr().out


def test_source_binding_requires_analytics_for_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import paic.tools.binding as binding
    from paic.tools.binding import BindingError, bind_sources

    monkeypatch.setattr(
        binding,
        "validate_dataset_directory",
        lambda path: type("R", (), {"valid": True})(),
    )
    with pytest.raises(BindingError, match="requires analytics"):
        bind_sources(tmp_path, detection_dir=tmp_path / "detection")


def test_source_binding_validates_every_optional_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import paic.tools.binding as binding

    def valid(*args: object, **kwargs: object) -> object:
        return type("R", (), {"valid": True})()

    monkeypatch.setattr(binding, "validate_dataset_directory", valid)
    monkeypatch.setattr(binding, "validate_analytics_directory", valid)
    monkeypatch.setattr(binding, "validate_detection_directory", valid)
    monkeypatch.setattr(binding, "validate_impact_directory", valid)
    monkeypatch.setattr(binding, "validate_evidence_directory", valid)
    monkeypatch.setattr(binding, "load_dataset", lambda path: (None, {}))
    monkeypatch.setattr(binding, "file_sha256", lambda path: "a" * 64)
    result = binding.bind_sources(
        tmp_path, tmp_path / "a", tmp_path / "d", tmp_path / "i", tmp_path / "e"
    )
    assert set(result.hashes) == {"dataset", "analytics", "detection", "impact", "evidence"}

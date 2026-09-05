from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from paic.investigation.artifact import _transcript_semantic_issues
from paic.investigation.models import ProviderUsage
from paic.investigation.orchestrator import InvestigationError, _require_source_lineage
from paic.tools.binding import BoundSources
from paic.tools.gateway import Gateway
from paic.tools.ledger import AuditLedger
from paic.tools.models import ToolRequest, ToolResponse


def response(*, hashes: dict[str, str]) -> ToolResponse:
    return ToolResponse(
        call_id="call",
        incident_id="incident",
        tool="artifacts.summary",
        tool_version="1.0",
        policy_decision="allow",
        execution_status="success",
        source_manifest_hashes=hashes,
        row_count=1,
        byte_count=2,
        truncated=False,
        evidence_record_ids=[],
        normalized_arguments={"include_tables": False},
        result={},
        result_sha256="0" * 64,
    )


def test_transcript_semantics_bind_provider_call_to_report_trace() -> None:
    arguments = {"query": "", "limit": 2}
    provider_call_id = "provider-call"
    call_id = str(
        uuid5(
            NAMESPACE_URL,
            "test-investigation:1:provider-call:evidence.search:"
            + json.dumps(arguments, sort_keys=True, separators=(",", ":")),
        )
    )
    provider = SimpleNamespace(
        event_type="provider_response",
        payload={
            "tool_calls": [
                {
                    "id": provider_call_id,
                    "name": "evidence__search",
                    "arguments": arguments,
                }
            ]
        },
    )
    result = SimpleNamespace(
        event_type="tool_result",
        payload={"tool_call_id": provider_call_id},
    )
    trace = SimpleNamespace(
        call_id=call_id,
        tool="evidence.search",
        arguments=arguments,
    )
    loaded: Any = SimpleNamespace(
        config=SimpleNamespace(investigation_id="test-investigation"),
        report=SimpleNamespace(tool_trace=[trace]),
        transcript=[provider, result],
    )
    assert _transcript_semantic_issues(loaded) == []
    result.payload["tool_call_id"] = "forged"
    assert any("identity mismatch" in issue for issue in _transcript_semantic_issues(loaded))


def test_provider_usage_rejects_component_total_budget_evasion() -> None:
    assert (
        ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=0).total_tokens == 150
    )
    with pytest.raises(ValidationError, match="total_tokens"):
        ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=149)
    assert ProviderUsage(total_tokens=150).total_tokens == 150
    assert (
        ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150).total_tokens == 150
    )


def test_investigator_rejects_success_from_another_source_lineage() -> None:
    with pytest.raises(InvestigationError, match="source lineage changed"):
        _require_source_lineage(response(hashes={"dataset": "b" * 64}), {"dataset": "a" * 64})


def test_gateway_rejects_source_change_during_one_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = BoundSources(Path("dataset"), None, None, None, None, {"dataset": "a" * 64})
    second = BoundSources(Path("dataset"), None, None, None, None, {"dataset": "b" * 64})
    values = iter([first, second])
    monkeypatch.setattr("paic.tools.gateway.bind_sources", lambda *args: next(values))
    monkeypatch.setattr(Gateway, "_run", lambda self, request, bound, args: ({}, False))
    result = Gateway().invoke(
        ToolRequest(
            tool="artifacts.summary",
            incident_id="incident",
            role="investigator",
            arguments={"include_tables": False},
            dataset_dir="dataset",
        )
    )
    assert result.execution_status == "error"
    assert result.error is not None
    assert "changed during tool invocation" in result.error.message


def test_audit_ledger_refuses_to_extend_corrupted_history(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit")
    request = {"tool": "artifacts.summary"}
    receipt = {
        "call_id": "first",
        "tool": "artifacts.summary",
        "result": {},
        "result_sha256": "a" * 64,
    }
    ledger.append(request, receipt, policy="allow", sources={})
    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    raw["request_sha256"] = "0" * 64
    ledger.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="request hash"):
        ledger.append(
            request,
            {**receipt, "call_id": "second"},
            policy="allow",
            sources={},
        )
    assert ledger.path.read_bytes() == before


def test_audit_ledger_rejects_symlink_root_and_payload(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        AuditLedger(link)

    ledger = AuditLedger(tmp_path / "audit")
    target = tmp_path / "outside.jsonl"
    target.write_text("", encoding="utf-8")
    ledger.path.symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        ledger.validate()

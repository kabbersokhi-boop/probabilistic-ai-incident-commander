from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paic.investigation.artifact import (
    export_investigation,
    replay_investigation,
    validate_investigation,
)
from paic.investigation.config import InvestigationConfig, ModelRoute
from paic.investigation.models import (
    InvestigationRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)
from paic.investigation.orchestrator import InvestigationError, Investigator, scripted_factory
from paic.investigation.provider import ChatProvider, ProviderError, ScriptedProvider
from paic.investigation.router import ModelRouter
from paic.tools.models import ToolRequest, ToolResponse

EVIDENCE_IDS = ["EVD-a", "EVD-b"]


def _config(*, rounds: int = 6) -> InvestigationConfig:
    return InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "unit-investigation",
            "provider": {
                "models": [
                    {
                        "model": "unit/model",
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "max_tokens": 1024,
                    }
                ]
            },
            "budget": {
                "max_rounds": rounds,
                "max_tool_calls": 4,
                "max_provider_failures": 2,
                "max_total_tokens": 1000,
                "max_tool_result_bytes": 10000,
            },
            "decision": {
                "minimum_top_posterior": 0.55,
                "minimum_margin": 0.15,
                "minimum_distinct_evidence": 2,
                "maximum_normalized_entropy": 0.9,
                "likelihood_ratio_min": 0.05,
                "likelihood_ratio_max": 20,
            },
            "allowed_tools": ["evidence.search"],
        }
    )


def _proposal(*, bad_id: bool = False) -> dict[str, Any]:
    first = "EVD-unseen" if bad_id else EVIDENCE_IDS[0]
    return {
        "summary": "The primary-service change is best supported.",
        "hypotheses": [
            {
                "hypothesis_id": "change",
                "title": "Primary-service change",
                "prior_probability": 0.5,
                "rationale": "Two records align.",
                "evidence": [
                    {
                        "evidence_record_id": first,
                        "direction": "support",
                        "likelihood_ratio": 5.0,
                        "explanation": "First record.",
                    },
                    {
                        "evidence_record_id": EVIDENCE_IDS[1],
                        "direction": "support",
                        "likelihood_ratio": 3.0,
                        "explanation": "Second record.",
                    },
                ],
                "falsifiers": ["No recovery after rollback."],
            },
            {
                "hypothesis_id": "other",
                "title": "Other service",
                "prior_probability": 0.5,
                "rationale": "Competing explanation.",
                "evidence": [
                    {
                        "evidence_record_id": EVIDENCE_IDS[1],
                        "direction": "contradict",
                        "likelihood_ratio": 0.2,
                        "explanation": "The record contradicts it.",
                    }
                ],
                "falsifiers": ["The other service remains healthy during the interval."],
            },
        ],
        "explicit_unknowns": ["Recovery is unverified."],
        "recommended_next_steps": ["Run a read-only recovery check."],
    }


class _FakeGateway:
    def invoke(self, request: ToolRequest) -> ToolResponse:
        arguments = request.arguments
        return ToolResponse.model_validate(
            {
                "call_id": str(request.call_id),
                "incident_id": "inc-unit",
                "tool": "evidence.search",
                "tool_version": "1.0",
                "policy_decision": "allow",
                "execution_status": "success",
                "source_manifest_hashes": {"dataset": "a" * 64},
                "row_count": 2,
                "byte_count": 100,
                "truncated": False,
                "evidence_record_ids": EVIDENCE_IDS,
                "normalized_arguments": arguments,
                "result": [
                    {"evidence_record_id": EVIDENCE_IDS[0], "summary": "first"},
                    {"evidence_record_id": EVIDENCE_IDS[1], "summary": "second"},
                ],
                "result_sha256": "b" * 64,
            }
        )


class _FailedEvidenceGateway(_FakeGateway):
    def invoke(self, request: ToolRequest) -> ToolResponse:
        return ToolResponse.model_validate(
            {
                "call_id": str(request.call_id),
                "incident_id": request.incident_id,
                "tool": request.tool,
                "tool_version": request.tool_version,
                "policy_decision": "allow",
                "execution_status": "error",
                "source_manifest_hashes": {"dataset": "a" * 64},
                "row_count": 0,
                "byte_count": 0,
                "truncated": False,
                # A compromised adapter must not make error metadata citeable.
                "evidence_record_ids": EVIDENCE_IDS,
                "normalized_arguments": request.arguments,
                "result": None,
                "result_sha256": "b" * 64,
                "error": {"code": "request_rejected", "message": "tool failed"},
            }
        )


def _responses() -> list[ProviderResponse]:
    return [
        ProviderResponse(
            model="unit/model",
            tool_calls=[
                ProviderToolCall(
                    id="search",
                    name="evidence__search",
                    arguments={"query": "checkout", "limit": 2},
                )
            ],
            usage=ProviderUsage(total_tokens=10),
        ),
        ProviderResponse(
            model="unit/model",
            tool_calls=[
                ProviderToolCall(
                    id="bad-submit",
                    name="submit_investigation",
                    arguments=_proposal(bad_id=True),
                )
            ],
            usage=ProviderUsage(total_tokens=10),
        ),
        ProviderResponse(
            model="unit/model",
            tool_calls=[
                ProviderToolCall(
                    id="submit",
                    name="submit_investigation",
                    arguments=_proposal(),
                )
            ],
            usage=ProviderUsage(total_tokens=10),
        ),
    ]


def test_isolated_investigation_export_validate_replay_and_tamper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    provider = ScriptedProvider("unit/model", _responses())
    investigator = Investigator(
        _config(),
        gateway=_FakeGateway(),
        provider_factory=scripted_factory({"unit/model": provider}),
    )
    request = InvestigationRequest(
        incident_id="inc-unit",
        question="What caused it?",
        dataset_dir="/not/read/by/unit/test",
    )
    report, transcript = investigator.run(request)
    assert report.status == "concluded"
    assert report.selected_hypothesis_id == "change"
    assert [event.event_type for event in transcript] == [
        "provider_response",
        "tool_result",
        "provider_response",
        "proposal_rejected",
        "provider_response",
        "proposal_accepted",
    ]

    output = tmp_path / "artifact"
    manifest = export_investigation(report, _config(), request, transcript, output)
    assert manifest.tool_call_count == 1
    assert validate_investigation(output) == []
    assert replay_investigation(output, artifact_only=True) == report

    transcript_path = output / "transcript.jsonl"
    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["payload"]["model"] = "forged/model"
    lines[0] = json.dumps(forged)
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert validate_investigation(output)


def test_parallel_calls_receive_protocol_safe_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    parallel = ProviderResponse.model_validate(
        {
            "model": "unit/model",
            "tool_calls": [
                {"id": "one", "name": "evidence__search", "arguments": {"limit": 2}},
                {"id": "two", "name": "evidence__search", "arguments": {"limit": 2}},
            ],
        }
    )
    provider = ScriptedProvider("unit/model", [parallel, *_responses()])
    report, _ = Investigator(
        _config(rounds=8),
        gateway=_FakeGateway(),
        provider_factory=scripted_factory({"unit/model": provider}),
    ).run(
        InvestigationRequest(
            incident_id="inc-unit",
            question="What caused it?",
            dataset_dir="/unused",
        )
    )
    assert report.status == "concluded"


def test_round_budget_exhaustion_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    provider = ScriptedProvider(
        "unit/model",
        [ProviderResponse(model="unit/model", content="no tool call")],
    )
    with pytest.raises(InvestigationError, match="round budget"):
        Investigator(
            _config(rounds=1),
            gateway=_FakeGateway(),
            provider_factory=scripted_factory({"unit/model": provider}),
        ).run(
            InvestigationRequest(
                incident_id="inc-unit",
                question="What caused it?",
                dataset_dir="/unused",
            )
        )


def test_failed_tool_response_cannot_supply_citeable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    responses = [_responses()[0], _responses()[-1]]
    with pytest.raises(InvestigationError, match="round budget"):
        Investigator(
            _config(rounds=2),
            gateway=_FailedEvidenceGateway(),
            provider_factory=scripted_factory(
                {"unit/model": ScriptedProvider("unit/model", responses)}
            ),
        ).run(
            InvestigationRequest(
                incident_id="inc-unit",
                question="What caused it?",
                dataset_dir="/unused",
            )
        )


class _RetryableFailure:
    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        raise ProviderError("rate_limited", "busy", retryable=True)


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        self.calls += 1
        return ProviderResponse(model="fallback", content="ok")


def test_router_sticks_to_first_healthy_fallback() -> None:
    config = _config().model_copy(
        update={
            "provider": _config().provider.model_copy(
                update={
                    "models": [
                        ModelRoute(model="primary"),
                        ModelRoute(model="fallback"),
                    ]
                }
            )
        }
    )
    fallback = _CountingProvider()

    def factory(route: ModelRoute) -> ChatProvider:
        return _RetryableFailure() if route.model == "primary" else fallback

    router = ModelRouter(config, factory)
    assert router.complete([], []).model == "fallback"
    assert router.complete([], []).model == "fallback"
    assert fallback.calls == 2
    assert sum(item.model == "primary" for item in router.attempts) == 1


def _refresh_manifest_file(root: Path, relative: str) -> None:
    from paic.simulator.io import file_sha256

    manifest_path = root / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = root / relative
    item = next(value for value in raw["files"] if value["relative_path"] == relative)
    item["byte_size"] = target.stat().st_size
    item["sha256"] = file_sha256(target)
    manifest_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")


def _unit_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    provider = ScriptedProvider("unit/model", [_responses()[0], _responses()[-1]])
    request = InvestigationRequest(
        incident_id="inc-unit", question="What caused it?", dataset_dir="/unused"
    )
    report, transcript = Investigator(
        _config(),
        gateway=_FakeGateway(),
        provider_factory=scripted_factory({"unit/model": provider}),
    ).run(request)
    output = tmp_path / "artifact-extra"
    export_investigation(report, _config(), request, transcript, output)
    return output


def test_artifact_semantic_tampering_and_source_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _unit_artifact(monkeypatch, tmp_path)
    receipt_path = output / "request.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["dataset_dir"] = "/secret/path"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _refresh_manifest_file(output, "request.receipt.json")
    assert any("source paths" in issue for issue in validate_investigation(output))

    monkeypatch.setattr(
        "paic.investigation.artifact.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "f" * 64}),
    )
    assert any(
        "source manifest hashes" in issue
        for issue in validate_investigation(output, dataset_dir="/unused")
    )


def test_artifact_rejects_missing_file_and_bad_success_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _unit_artifact(monkeypatch, tmp_path)
    (output / "transcript.jsonl").unlink()
    (output / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    issues = validate_investigation(output)
    assert any("missing or undeclared" in issue for issue in issues)

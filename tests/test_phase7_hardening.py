from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from paic.investigation.artifact import (
    InvestigationArtifactError,
    _safe_path,
    export_investigation,
    load_investigation,
    validate_investigation,
)
from paic.investigation.config import (
    DecisionPolicy,
    InvestigationConfig,
    InvestigationConfigError,
    ModelRoute,
    load_investigation_config,
)
from paic.investigation.evaluation import evaluate_cases
from paic.investigation.models import (
    ChatMessage,
    EvidenceAssessment,
    HypothesisProposal,
    InvestigationProposal,
    InvestigationRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
    TranscriptEvent,
)
from paic.investigation.orchestrator import (
    InvestigationError,
    Investigator,
    _event,
    _provider_event_payload,
    scripted_factory,
)
from paic.investigation.probability import score_proposal
from paic.investigation.provider import NvidiaNIMProvider, ProviderError, ScriptedProvider
from paic.investigation.router import ModelRouter
from paic.tools.gateway import Gateway, GatewayError, _evidence_refs
from paic.tools.ledger import AuditLedger, canonical
from paic.tools.models import ToolRequest, ToolResponse
from paic.tools.policy import authorize
from paic.tools.sql import SQLPolicyError, validate_sql


def _config(*, models: list[dict[str, Any]] | None = None, rounds: int = 4) -> InvestigationConfig:
    return InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "hardening-test",
            "provider": {"models": models or [{"model": "unit/model"}]},
            "budget": {
                "max_rounds": rounds,
                "max_tool_calls": 2,
                "max_provider_failures": 1,
                "max_total_tokens": 512,
                "max_tool_result_bytes": 5_000,
            },
            "allowed_tools": ["evidence.search"],
        }
    )


def _proposal() -> InvestigationProposal:
    return InvestigationProposal.model_validate(
        {
            "summary": "A bounded conclusion.",
            "hypotheses": [
                {
                    "hypothesis_id": "change",
                    "title": "Relevant change",
                    "prior_probability": 0.5,
                    "rationale": "Evidence aligns.",
                    "evidence": [
                        {
                            "evidence_record_id": "E1",
                            "direction": "support",
                            "likelihood_ratio": 4.0,
                            "explanation": "Temporal alignment.",
                        },
                        {
                            "evidence_record_id": "E2",
                            "direction": "support",
                            "likelihood_ratio": 2.0,
                            "explanation": "Cohort alignment.",
                        },
                    ],
                    "falsifiers": ["Expected corroboration is absent."],
                },
                {
                    "hypothesis_id": "other",
                    "title": "Other cause",
                    "prior_probability": 0.5,
                    "rationale": "Competing explanation.",
                    "evidence": [
                        {
                            "evidence_record_id": "E2",
                            "direction": "contradict",
                            "likelihood_ratio": 0.5,
                            "explanation": "Contradictory observation.",
                        }
                    ],
                    "falsifiers": ["A competing cause fully explains the timing."],
                },
            ],
        }
    )


def _report() -> tuple[InvestigationConfig, InvestigationRequest, Any]:
    config = _config()
    request = InvestigationRequest(
        incident_id="inc-hardening", question="Why?", dataset_dir="/unused"
    )
    report = score_proposal(
        _proposal(),
        investigation_id=config.investigation_id,
        incident_id=request.incident_id,
        question=request.question,
        policy=DecisionPolicy(minimum_distinct_evidence=2),
        observed_evidence={"E1", "E2"},
        source_hashes={"dataset": "a" * 64},
        attempts=[],
        trace=[],
        total_tokens=0,
    )
    return config, request, report


def test_config_validation_and_loader_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires enable_thinking"):
        ModelRoute(model="x", reasoning_budget=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        ModelRoute(model="x", enable_thinking=True, max_tokens=128, reasoning_budget=129)
    for base_url in (
        "http://example.com/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?token=x",
    ):
        invalid_provider = _config().model_dump(mode="json")
        invalid_provider["provider"]["base_url"] = base_url
        with pytest.raises(ValueError):
            InvestigationConfig.model_validate(invalid_provider)
    empty_models = _config().model_dump(mode="json")
    empty_models["provider"]["models"] = []
    with pytest.raises(ValueError, match="at least one model"):
        InvestigationConfig.model_validate(empty_models)
    duplicate_models = _config().model_dump(mode="json")
    duplicate_models["provider"]["models"] = [{"model": "x"}, {"model": "x"}]
    with pytest.raises(ValueError, match="unique"):
        InvestigationConfig.model_validate(duplicate_models)
    base = _config().model_dump(mode="json")
    for tools, message in (
        ([], "at least one"),
        (["evidence.search"] * 2, "unique"),
        (["x"], "unknown"),
    ):
        raw = dict(base)
        raw["allowed_tools"] = tools
        with pytest.raises(ValueError, match=message):
            InvestigationConfig.model_validate(raw)
    with pytest.raises(InvestigationConfigError, match="cannot read"):
        load_investigation_config(tmp_path / "missing.yaml")
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("[unclosed", encoding="utf-8")
    with pytest.raises(InvestigationConfigError, match="invalid YAML"):
        load_investigation_config(invalid_yaml)
    invalid_config = tmp_path / "invalid-config.yaml"
    invalid_config.write_text(
        "schema_version: '1.0'\ninvestigation_id: x\nprovider:\n  models: []\n"
    )
    with pytest.raises(InvestigationConfigError, match="invalid investigation config"):
        load_investigation_config(invalid_config)


def test_proposal_models_reject_direction_duplicates_and_bad_priors() -> None:
    with pytest.raises(ValueError, match="supporting"):
        EvidenceAssessment(
            evidence_record_id="E1",
            direction="support",
            likelihood_ratio=0.5,
            explanation="bad",
        )
    with pytest.raises(ValueError, match="contradicting"):
        EvidenceAssessment(
            evidence_record_id="E1",
            direction="contradict",
            likelihood_ratio=2.0,
            explanation="bad",
        )
    hypothesis = _proposal().hypotheses[0].model_dump(mode="json")
    hypothesis["evidence"] = [hypothesis["evidence"][0], hypothesis["evidence"][0]]
    with pytest.raises(ValueError, match="unique"):
        HypothesisProposal.model_validate(hypothesis)
    raw = _proposal().model_dump(mode="json")
    raw["hypotheses"][1]["hypothesis_id"] = "change"
    with pytest.raises(ValueError, match="unique"):
        InvestigationProposal.model_validate(raw)
    raw = _proposal().model_dump(mode="json")
    raw["hypotheses"][0]["prior_probability"] = 0.7
    with pytest.raises(ValueError, match="sum to 1"):
        InvestigationProposal.model_validate(raw)


def test_investigation_quality_contract_rejects_invalid_proposals() -> None:
    raw = _proposal().model_dump(mode="json")
    raw["hypotheses"][0]["falsifiers"] = []
    with pytest.raises(ValueError, match="at least 1"):
        InvestigationProposal.model_validate(raw)
    raw = _proposal().model_dump(mode="json")
    raw["hypotheses"][0]["falsifiers"] = ["same", " same "]
    with pytest.raises(ValueError, match="falsifiers must be unique"):
        InvestigationProposal.model_validate(raw)
    raw = _proposal().model_dump(mode="json")
    raw["explicit_unknowns"] = [" "]
    with pytest.raises(ValueError, match="explicit_unknowns must be non-blank"):
        InvestigationProposal.model_validate(raw)
    raw = _proposal().model_dump(mode="json")
    raw["recommended_next_steps"] = ["x", " x "]
    with pytest.raises(ValueError, match="recommended_next_steps must be unique"):
        InvestigationProposal.model_validate(raw)
    abstaining = _proposal().model_copy(
        update={"explicit_unknowns": [], "recommended_next_steps": []}
    )
    with pytest.raises(Exception, match="explicit unknown"):
        score_proposal(
            abstaining,
            investigation_id="quality",
            incident_id="inc",
            question="why",
            policy=DecisionPolicy(minimum_top_posterior=1.0),
            observed_evidence={"E1", "E2"},
            source_hashes={},
            attempts=[],
            trace=[],
            total_tokens=0,
        )


class _HTTPResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


def test_provider_family_fields_strict_json_transport_and_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY_TEST", "ephemeral")
    generic = _config(models=[{"model": "qwen/qwen3.5-122b-a10b"}]).provider.model_copy(
        update={"api_key_env": "NVIDIA_API_KEY_TEST"}
    )
    captured: list[dict[str, Any]] = []

    def respond(request: Any, *args: object, **kwargs: object) -> _HTTPResponse:
        del args, kwargs
        captured.append(json.loads(request.data.decode()))
        return _HTTPResponse(
            json.dumps(
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            ).encode()
        )

    monkeypatch.setattr("paic.investigation.provider.urllib.request.urlopen", respond)
    NvidiaNIMProvider(generic, generic.models[0]).complete(
        [ChatMessage(role="user", content="x")], []
    )
    assert captured[-1]["chat_template_kwargs"] == {"enable_thinking": False}

    nemotron = _config(
        models=[
            {
                "model": "nvidia/nemotron-3-super-120b-a12b",
                "enable_thinking": True,
                "reasoning_budget": 128,
                "max_tokens": 256,
            }
        ]
    ).provider.model_copy(update={"api_key_env": "NVIDIA_API_KEY_TEST"})
    NvidiaNIMProvider(nemotron, nemotron.models[0]).complete(
        [ChatMessage(role="user", content="x")],
        [{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )
    assert captured[-1]["reasoning_budget"] == 128
    assert captured[-1]["chat_template_kwargs"]["enable_thinking"] is True
    assert captured[-1]["parallel_tool_calls"] is False

    nano = _config(
        models=[{"model": "nvidia/nemotron-3-nano-30b-a3b", "enable_thinking": True}]
    ).provider.model_copy(update={"api_key_env": "NVIDIA_API_KEY_TEST"})
    NvidiaNIMProvider(nano, nano.models[0]).complete([ChatMessage(role="user", content="x")], [])
    assert captured[-1]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "reasoning_budget" not in captured[-1]
    assert "force_nonempty_content" not in captured[-1]["chat_template_kwargs"]

    malformed_payloads = [
        b'{"choices":[],"choices":[]}',
        json.dumps({"choices": [{"message": {"content": ["not", "text"]}}]}).encode(),
        json.dumps(
            {
                "choices": [{"message": {"content": "x"}}],
                "usage": {"total_tokens": "not-an-int"},
            }
        ).encode(),
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "t",
                                    "function": {
                                        "name": "x",
                                        "arguments": '{"x":1,"x":2}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode(),
    ]
    for payload in malformed_payloads:
        monkeypatch.setattr(
            "paic.investigation.provider.urllib.request.urlopen",
            lambda *args, _payload=payload, **kwargs: _HTTPResponse(_payload),
        )
        with pytest.raises(ProviderError):
            NvidiaNIMProvider(generic, generic.models[0]).complete([], [])

    monkeypatch.setattr(
        "paic.investigation.provider.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    with pytest.raises(ProviderError, match="failed") as transport:
        NvidiaNIMProvider(generic, generic.models[0]).complete([], [])
    assert transport.value.retryable

    scripted = ScriptedProvider("unit/model", [ProviderResponse(model="x", content="ok")])
    assert scripted.complete([], []).content == "ok"
    with pytest.raises(ProviderError, match="no remaining"):
        scripted.complete([], [])


class _Retryable:
    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        raise ProviderError("busy", "busy", retryable=True)


class _Fatal:
    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        raise ProviderError("bad_auth", "bad", retryable=False)


def test_router_records_retry_budget_and_fatal_errors() -> None:
    retry_config = _config(models=[{"model": "one"}, {"model": "two"}]).model_copy(
        update={"budget": _config().budget.model_copy(update={"max_provider_failures": 0})}
    )
    router = ModelRouter(retry_config, lambda route: _Retryable())
    with pytest.raises(ProviderError):
        router.complete([], [])
    assert router.attempts[0].status == "retryable_error"

    fatal_router = ModelRouter(_config(), lambda route: _Fatal())
    with pytest.raises(ProviderError):
        fatal_router.complete([], [])
    assert fatal_router.attempts[0].status == "fatal_error"


class _FakeGateway:
    def invoke(self, request: ToolRequest) -> ToolResponse:
        return ToolResponse.model_validate(
            {
                "call_id": str(request.call_id),
                "incident_id": request.incident_id,
                "tool": request.tool,
                "tool_version": request.tool_version,
                "policy_decision": "allow",
                "execution_status": "success",
                "source_manifest_hashes": {"dataset": "a" * 64},
                "row_count": 1,
                "byte_count": 10,
                "truncated": False,
                "evidence_record_ids": ["E1", "E2"],
                "normalized_arguments": request.arguments,
                "result": [{"evidence_record_id": "E1"}],
                "result_sha256": "b" * 64,
            }
        )


def test_orchestrator_rejects_invalid_provider_token_and_tool_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paic.investigation.orchestrator.bind_sources",
        lambda *args, **kwargs: SimpleNamespace(hashes={"dataset": "a" * 64}),
    )
    with pytest.raises(InvestigationError, match="invalid investigation request"):
        Investigator(_config()).run({})

    failing = ModelRouter
    del failing
    with pytest.raises(InvestigationError, match="all model routes failed"):
        Investigator(
            _config(), provider_factory=lambda route: _Fatal(), gateway=_FakeGateway()
        ).run(InvestigationRequest(incident_id="i", question="q", dataset_dir="/unused"))

    token_provider = ScriptedProvider(
        "unit/model",
        [ProviderResponse(model="unit/model", content="x", usage=ProviderUsage(total_tokens=513))],
    )
    with pytest.raises(InvestigationError, match="token budget"):
        Investigator(
            _config(rounds=1),
            provider_factory=scripted_factory({"unit/model": token_provider}),
            gateway=_FakeGateway(),
        ).run(InvestigationRequest(incident_id="i", question="q", dataset_dir="/unused"))

    calls = [
        ProviderResponse(
            model="unit/model",
            tool_calls=[
                ProviderToolCall(id="one", name="evidence__search", arguments={"limit": 1})
            ],
        ),
        ProviderResponse(
            model="unit/model",
            tool_calls=[
                ProviderToolCall(id="two", name="evidence__search", arguments={"limit": 1})
            ],
        ),
    ]
    limited = _config(rounds=2).model_copy(
        update={"budget": _config().budget.model_copy(update={"max_tool_calls": 1})}
    )
    with pytest.raises(InvestigationError, match="tool-call budget"):
        Investigator(
            limited,
            provider_factory=scripted_factory(
                {"unit/model": ScriptedProvider("unit/model", calls)}
            ),
            gateway=_FakeGateway(),
        ).run(InvestigationRequest(incident_id="i", question="q", dataset_dir="/unused"))


def test_artifact_export_path_and_load_failures(tmp_path: Path) -> None:
    config, request, report = _report()
    base = {
        "sequence": 1,
        "event_type": "proposal_accepted",
        "payload": {"report_sha256": report.report_sha256},
        "previous_event_sha256": "0" * 64,
    }
    import hashlib

    event = TranscriptEvent.model_validate(
        {**base, "event_sha256": hashlib.sha256(canonical(base).encode()).hexdigest()}
    )
    output = tmp_path / "artifact"
    export_investigation(report, config, request, [event], output)
    with pytest.raises(InvestigationArtifactError, match="already exists"):
        export_investigation(report, config, request, [event], output)
    file_target = tmp_path / "file"
    file_target.write_text("x", encoding="utf-8")
    with pytest.raises(InvestigationArtifactError, match="output path is a file"):
        export_investigation(report, config, request, [event], file_target, overwrite=True)
    with pytest.raises(InvestigationArtifactError, match="unsafe"):
        _safe_path(output, "../outside")
    with pytest.raises(InvestigationArtifactError, match="root is not"):
        load_investigation(tmp_path / "missing")


def test_artifact_validation_is_closed_world(tmp_path: Path) -> None:
    config, request, report = _report()
    base = {
        "sequence": 1,
        "event_type": "proposal_accepted",
        "payload": {"report_sha256": report.report_sha256},
        "previous_event_sha256": "0" * 64,
    }
    event = TranscriptEvent.model_validate(
        {**base, "event_sha256": __import__("hashlib").sha256(canonical(base).encode()).hexdigest()}
    )
    output = tmp_path / "artifact"
    export_investigation(report, config, request, [event], output)
    (output / "extra.txt").write_text("x", encoding="utf-8")
    assert validate_investigation(output)
    (output / "extra.txt").unlink()
    (output / "nested").mkdir()
    assert validate_investigation(output)
    (output / "nested").rmdir()
    try:
        (output / "report.json").unlink()
        (output / "report.json").symlink_to(output / "manifest.json")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    assert validate_investigation(output)


def test_exported_transcript_never_persists_provider_free_form_content(tmp_path: Path) -> None:
    config, request, report = _report()
    marker = "nvapi-EXAMPLE-DO-NOT-PERSIST"
    events: list[TranscriptEvent] = []
    _event(
        events,
        "provider_response",
        _provider_event_payload(
            ProviderResponse(
                model="unit",
                content=marker,
                tool_calls=[
                    ProviderToolCall(
                        id="submit",
                        name="submit_investigation",
                        arguments={},
                    )
                ],
            )
        ),
    )
    _event(
        events,
        "proposal_accepted",
        {
            "tool_call_id": "submit",
            "report_sha256": report.report_sha256,
            "status": report.status,
        },
    )
    output = tmp_path / "artifact"
    export_investigation(report, config, request, events, output)
    assert marker not in "".join(
        path.read_text(encoding="utf-8") for path in output.iterdir() if path.is_file()
    )
    assert not validate_investigation(output)


def test_gateway_sql_policy_ledger_and_empty_evaluation_edges(tmp_path: Path) -> None:
    assert evaluate_cases([]).case_count == 0
    for decision in (
        authorize("unknown", "evidence.search", "1.0", {}),
        authorize("observer", "unknown", "1.0", {}),
        authorize("observer", "evidence.search", "2.0", {}),
    ):
        assert not decision.allowed
    with pytest.raises(ValueError, match="positive"):
        Gateway(row_limit=0)
    gateway = Gateway(row_limit=1, byte_limit=5)
    assert gateway._bounded([1, 2])[1]
    with pytest.raises(GatewayError, match="byte limit"):
        gateway._bounded({"too": "large"})
    assert _evidence_refs(
        {"evidence_record_id": "E1", "nested": [{"evidence_record_ids_json": "not-json"}]}
    ) == ["E1"]
    assert gateway._lineage_trace({}, {"node_id": "x", "direction": "both", "depth": 1}) == {
        "nodes": [],
        "edges": [],
    }
    with pytest.raises(GatewayError, match="unknown lineage"):
        gateway._lineage_trace(
            {
                "evidence__lineage_nodes": pl.DataFrame({"node_id": ["a"]}),
                "evidence__lineage_edges": pl.DataFrame(
                    {"edge_id": ["e"], "upstream_node_id": ["a"], "downstream_node_id": ["a"]}
                ),
            },
            {"node_id": "missing", "direction": "both", "depth": 1},
        )

    columns = {"data": {"id"}}
    attacks = [
        "",
        "not sql",
        "VALUES (1)",
        "SELECT * FROM main.data",
        "SELECT x.id FROM data",
        "SELECT missing FROM data",
    ]
    for query in attacks:
        with pytest.raises(SQLPolicyError):
            validate_sql(query, {"data"}, columns)
    with pytest.raises(SQLPolicyError, match="complexity"):
        validate_sql(
            "SELECT id FROM data WHERE id IN (SELECT id FROM data)",
            {"data"},
            columns,
            max_complexity=1,
        )

    ledger = AuditLedger(tmp_path / "ledger")
    response = {
        "call_id": "c",
        "tool": "artifacts.summary",
        "result": {},
        "result_sha256": "a" * 64,
        "execution_status": "success",
    }
    ledger.append({"tool": "artifacts.summary"}, response, policy="allow", sources={})
    ledger.path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        ledger.validate()

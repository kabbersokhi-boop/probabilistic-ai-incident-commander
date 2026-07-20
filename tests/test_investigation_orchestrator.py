from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.artifacts.publication import AtomicDirectoryPublisher
from paic.evidence.io import load_evidence
from paic.investigation.artifact import (
    InvestigationArtifactError,
    export_investigation,
    replay_investigation,
    validate_investigation,
)
from paic.investigation.config import InvestigationConfig, ModelRoute
from paic.investigation.models import InvestigationRequest, ProviderResponse
from paic.investigation.orchestrator import Investigator, scripted_factory
from paic.investigation.provider import ChatProvider, ProviderError, ScriptedProvider
from paic.tools.gateway import Gateway


def _config() -> InvestigationConfig:
    return InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "offline-investigation",
            "provider": {
                "models": [
                    {
                        "model": "test/model",
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "max_tokens": 2048,
                    }
                ]
            },
            "budget": {
                "max_rounds": 6,
                "max_tool_calls": 4,
                "max_provider_failures": 2,
                "max_total_tokens": 10000,
                "max_tool_result_bytes": 50000,
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


def _responses(evidence_ids: list[str], *, bad_first: bool = False) -> list[ProviderResponse]:
    responses = [
        ProviderResponse.model_validate(
            {
                "model": "test/model",
                "tool_calls": [
                    {
                        "id": "call-search",
                        "name": "evidence__search",
                        "arguments": {"query": "", "limit": 2},
                    }
                ],
                "usage": {"total_tokens": 10},
            }
        )
    ]
    if bad_first:
        responses.append(
            ProviderResponse.model_validate(
                {
                    "model": "test/model",
                    "tool_calls": [
                        {
                            "id": "call-bad-submit",
                            "name": "submit_investigation",
                            "arguments": {
                                "summary": "bad",
                                "hypotheses": [
                                    {
                                        "hypothesis_id": "a",
                                        "title": "A",
                                        "prior_probability": 0.5,
                                        "rationale": "A",
                                        "evidence": [
                                            {
                                                "evidence_record_id": "EVD-not-observed",
                                                "direction": "support",
                                                "likelihood_ratio": 2,
                                                "explanation": "bad",
                                            }
                                        ],
                                        "falsifiers": [
                                            "The cited event is not present in governed output."
                                        ],
                                    },
                                    {
                                        "hypothesis_id": "b",
                                        "title": "B",
                                        "prior_probability": 0.5,
                                        "rationale": "B",
                                        "evidence": [
                                            {
                                                "evidence_record_id": evidence_ids[0],
                                                "direction": "support",
                                                "likelihood_ratio": 2,
                                                "explanation": "observed",
                                            }
                                        ],
                                        "falsifiers": [
                                            "The observed record contradicts this explanation."
                                        ],
                                    },
                                ],
                            },
                        }
                    ],
                    "usage": {"total_tokens": 10},
                }
            )
        )
    responses.append(
        ProviderResponse.model_validate(
            {
                "model": "test/model",
                "tool_calls": [
                    {
                        "id": "call-submit",
                        "name": "submit_investigation",
                        "arguments": {
                            "summary": "A recent primary-service change best explains the incident.",
                            "hypotheses": [
                                {
                                    "hypothesis_id": "change-regression",
                                    "title": "Primary-service change regression",
                                    "prior_probability": 0.5,
                                    "rationale": "Two independent evidence records align.",
                                    "evidence": [
                                        {
                                            "evidence_record_id": evidence_ids[0],
                                            "direction": "support",
                                            "likelihood_ratio": 5,
                                            "explanation": "Relevant evidence record.",
                                        },
                                        {
                                            "evidence_record_id": evidence_ids[1],
                                            "direction": "support",
                                            "likelihood_ratio": 3,
                                            "explanation": "Second relevant record.",
                                        },
                                    ],
                                    "falsifiers": ["No recovery after reverting the change"],
                                },
                                {
                                    "hypothesis_id": "unrelated-service",
                                    "title": "Unrelated service degradation",
                                    "prior_probability": 0.5,
                                    "rationale": "Considered as a competing explanation.",
                                    "evidence": [
                                        {
                                            "evidence_record_id": evidence_ids[1],
                                            "direction": "contradict",
                                            "likelihood_ratio": 0.2,
                                            "explanation": "Evidence does not align with that service.",
                                        }
                                    ],
                                    "falsifiers": [
                                        "The unrelated service shows a matching regression."
                                    ],
                                },
                            ],
                            "explicit_unknowns": ["Recovery has not yet been measured."],
                            "recommended_next_steps": [
                                "Run a read-only post-change cohort comparison."
                            ],
                        },
                    }
                ],
                "usage": {"total_tokens": 20},
            }
        )
    )
    return responses


def test_offline_tool_loop_rejects_unsupported_claim_then_concludes(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    provider = ScriptedProvider("test/model", _responses(ids, bad_first=True))
    request = InvestigationRequest(
        incident_id="checkout-address-validation-smoke",
        question="What caused the incident?",
        dataset_dir=str(impact_smoke_dataset_dir),
        evidence_dir=str(evidence_smoke_dir),
        audit_dir=str(tmp_path / "tool-audit"),
    )
    report, transcript = Investigator(
        _config(), provider_factory=scripted_factory({"test/model": provider})
    ).run(request)
    assert report.status == "concluded"
    assert report.selected_hypothesis_id == "change-regression"
    assert any(event.event_type == "proposal_rejected" for event in transcript)
    assert report.tool_trace[0].tool == "evidence.search"
    assert set(ids) <= set(report.observed_evidence_record_ids)

    artifact = tmp_path / "investigation"
    manifest = export_investigation(report, _config(), request, transcript, artifact)
    assert manifest.status == "concluded"
    assert not validate_investigation(
        artifact,
        dataset_dir=impact_smoke_dataset_dir,
        evidence_dir=evidence_smoke_dir,
    )
    with pytest.raises(InvestigationArtifactError, match="original investigation config"):
        replay_investigation(artifact)
    assert replay_investigation(artifact, artifact_only=True) == report
    config_path = tmp_path / "investigation-config.json"
    config_path.write_text(_config().model_dump_json(), encoding="utf-8")
    assert (
        replay_investigation(
            artifact,
            dataset_dir=impact_smoke_dataset_dir,
            evidence_dir=evidence_smoke_dir,
            config_path=config_path,
        )
        == report
    )
    original_invoke = Gateway.invoke

    def altered_invoke(self: Gateway, request: object) -> object:
        response = original_invoke(self, request)  # type: ignore[arg-type]
        if response.execution_status == "success":
            return response.model_copy(update={"result_sha256": "f" * 64})
        return response

    monkeypatch.setattr(Gateway, "invoke", altered_invoke)
    with pytest.raises(InvestigationArtifactError, match="semantic replay mismatch"):
        replay_investigation(
            artifact,
            dataset_dir=impact_smoke_dataset_dir,
            evidence_dir=evidence_smoke_dir,
            config_path=config_path,
        )
    monkeypatch.setattr(Gateway, "invoke", original_invoke)

    report_path = artifact / "report.json"
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    raw["confidence"] = 0.0
    report_path.write_text(json.dumps(raw), encoding="utf-8")
    assert validate_investigation(artifact)
    with pytest.raises(InvestigationArtifactError, match="validation failed"):
        replay_investigation(artifact, artifact_only=True)


def test_governed_tool_denial_can_recover_and_authoritatively_replay(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
    tmp_path: Path,
) -> None:
    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    invalid_call = ProviderResponse.model_validate(
        {
            "model": "test/model",
            "tool_calls": [
                {
                    "id": "call-invalid-search",
                    "name": "evidence__search",
                    "arguments": {"query": "", "limit": 0},
                }
            ],
            "usage": {"total_tokens": 10},
        }
    )
    provider = ScriptedProvider("test/model", [invalid_call, *_responses(ids)])
    request = InvestigationRequest(
        incident_id="checkout-address-validation-smoke",
        question="What caused the incident?",
        dataset_dir=str(impact_smoke_dataset_dir),
        evidence_dir=str(evidence_smoke_dir),
        audit_dir=str(tmp_path / "tool-audit"),
    )
    config = _config()
    report, transcript = Investigator(
        config, provider_factory=scripted_factory({"test/model": provider})
    ).run(request)

    assert report.status == "concluded"
    assert report.tool_trace[0].execution_status == "error"
    assert report.tool_trace[0].arguments == {}
    assert report.tool_trace[0].error_code == "invalid_arguments"
    assert report.tool_trace[1].execution_status == "success"
    artifact = tmp_path / "investigation"
    export_investigation(report, config, request, transcript, artifact)
    assert not validate_investigation(
        artifact,
        dataset_dir=impact_smoke_dataset_dir,
        evidence_dir=evidence_smoke_dir,
    )
    config_path = tmp_path / "investigation-config.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    assert (
        replay_investigation(
            artifact,
            dataset_dir=impact_smoke_dataset_dir,
            evidence_dir=evidence_smoke_dir,
            config_path=config_path,
        )
        == report
    )


def test_investigation_publication_preserves_replayable_generation(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    config = _config()
    request = InvestigationRequest(
        incident_id="checkout-address-validation-smoke",
        question="What caused the incident?",
        dataset_dir=str(impact_smoke_dataset_dir),
        evidence_dir=str(evidence_smoke_dir),
        audit_dir=str(tmp_path / "audit"),
    )
    report, transcript = Investigator(
        config,
        provider_factory=scripted_factory(
            {"test/model": ScriptedProvider("test/model", _responses(ids))}
        ),
    ).run(request)
    output = tmp_path / "investigation"
    export_investigation(report, config, request, transcript, output)
    previous = (output / "manifest.json").read_bytes()

    def before_commit(point: str) -> None:
        if point == "payload-written":
            raise RuntimeError("before commit")

    class BeforeCommitPublisher(AtomicDirectoryPublisher):
        def __init__(self, target: str | Path, *, overwrite: bool = False) -> None:
            super().__init__(target, overwrite=overwrite, failure_hook=before_commit)

    monkeypatch.setattr(
        "paic.investigation.artifact.AtomicDirectoryPublisher", BeforeCommitPublisher
    )
    with pytest.raises(RuntimeError, match="before commit"):
        export_investigation(report, config, request, transcript, output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == previous
    assert (
        validate_investigation(
            output, dataset_dir=impact_smoke_dataset_dir, evidence_dir=evidence_smoke_dir
        )
        == []
    )
    assert not list(tmp_path.glob(".investigation.staging-*"))
    assert not list(tmp_path.glob(".investigation.backup-*"))

    monkeypatch.setattr(
        "paic.investigation.artifact.AtomicDirectoryPublisher", AtomicDirectoryPublisher
    )
    export_investigation(report, config, request, transcript, output, overwrite=True)
    assert (
        validate_investigation(
            output, dataset_dir=impact_smoke_dataset_dir, evidence_dir=evidence_smoke_dir
        )
        == []
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    assert (
        replay_investigation(
            output,
            dataset_dir=impact_smoke_dataset_dir,
            evidence_dir=evidence_smoke_dir,
            config_path=config_path,
        )
        == report
    )

    real = tmp_path / "real-investigation"
    real.mkdir()
    link = tmp_path / "investigation-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(InvestigationArtifactError, match="symbolic link"):
        export_investigation(report, config, request, transcript, link, overwrite=True)
    file_target = tmp_path / "investigation-file"
    file_target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(InvestigationArtifactError, match="artifact target must be a directory"):
        export_investigation(report, config, request, transcript, file_target, overwrite=True)

    def after_visibility(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("after visibility")

    class AfterVisibilityPublisher(AtomicDirectoryPublisher):
        def __init__(self, target: str | Path, *, overwrite: bool = False) -> None:
            super().__init__(target, overwrite=overwrite, failure_hook=after_visibility)

    monkeypatch.setattr(
        "paic.investigation.artifact.AtomicDirectoryPublisher", AfterVisibilityPublisher
    )
    with pytest.raises(InvestigationArtifactError, match="committed but durability is uncertain"):
        export_investigation(report, config, request, transcript, output, overwrite=True)
    assert (
        validate_investigation(
            output, dataset_dir=impact_smoke_dataset_dir, evidence_dir=evidence_smoke_dir
        )
        == []
    )


class _FailureProvider:
    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        raise ProviderError("rate_limited", "busy", retryable=True)


def test_model_router_falls_back_after_retryable_failure(
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
) -> None:
    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    config = _config().model_copy(
        update={
            "provider": _config().provider.model_copy(
                update={
                    "models": [
                        _config().provider.models[0].model_copy(update={"model": "primary"}),
                        _config().provider.models[0].model_copy(update={"model": "fallback"}),
                    ]
                }
            )
        }
    )
    fallback = ScriptedProvider("fallback", _responses(ids))

    def factory(route: ModelRoute) -> ChatProvider:
        return _FailureProvider() if route.model == "primary" else fallback

    request = InvestigationRequest(
        incident_id="checkout-address-validation-smoke",
        question="What caused the incident?",
        dataset_dir=str(impact_smoke_dataset_dir),
        evidence_dir=str(evidence_smoke_dir),
    )
    report, _ = Investigator(config, provider_factory=factory).run(request)
    assert report.status == "concluded"
    assert report.model_attempts[0].model == "primary"
    assert report.model_attempts[0].status == "retryable_error"
    assert any(
        item.model == "fallback" and item.status == "success" for item in report.model_attempts
    )
    assert sum(item.model == "primary" for item in report.model_attempts) == 1

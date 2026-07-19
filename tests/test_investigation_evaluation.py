from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paic.investigation.artifact import InvestigationArtifactError, export_investigation
from paic.investigation.config import InvestigationConfig
from paic.investigation.evaluation import EvaluationCase, evaluate_cases
from paic.investigation.models import (
    InvestigationProposal,
    InvestigationReport,
    InvestigationRequest,
    TranscriptEvent,
)
from paic.investigation.orchestrator import _event
from paic.investigation.probability import score_proposal


def _export_case(tmp_path: Path, report: InvestigationReport) -> Path:
    config = InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "eval",
            "provider": {"models": [{"model": "unit"}]},
            "decision": {"minimum_distinct_evidence": 1},
        }
    )
    events: list[TranscriptEvent] = []
    _event(
        events,
        "provider_response",
        {"tool_calls": [{"id": "submit", "name": "submit_investigation", "arguments": {}}]},
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
    output = tmp_path / "investigation"
    export_investigation(
        report,
        config,
        InvestigationRequest(incident_id="inc", question="why", dataset_dir="/unused"),
        events,
        output,
    )
    return output


def test_evaluation_metrics_are_reproducible(tmp_path: Path) -> None:
    proposal = InvestigationProposal.model_validate(
        {
            "summary": "A is most likely.",
            "hypotheses": [
                {
                    "hypothesis_id": "a",
                    "title": "A",
                    "prior_probability": 0.5,
                    "rationale": "A",
                    "evidence": [
                        {
                            "evidence_record_id": "E1",
                            "direction": "support",
                            "likelihood_ratio": 4,
                            "explanation": "supports A",
                        }
                    ],
                    "falsifiers": ["A has no expected corroborating signal."],
                },
                {
                    "hypothesis_id": "b",
                    "title": "B",
                    "prior_probability": 0.5,
                    "rationale": "B",
                    "evidence": [
                        {
                            "evidence_record_id": "E1",
                            "direction": "contradict",
                            "likelihood_ratio": 0.25,
                            "explanation": "contradicts B",
                        }
                    ],
                    "falsifiers": ["B would show a distinct service health regression."],
                },
            ],
        }
    )
    report = score_proposal(
        proposal,
        investigation_id="eval",
        incident_id="inc",
        question="why",
        policy=__import__("paic.investigation.config", fromlist=["DecisionPolicy"]).DecisionPolicy(
            minimum_distinct_evidence=1
        ),
        observed_evidence={"E1"},
        source_hashes={},
        attempts=[],
        trace=[],
        total_tokens=0,
    )
    path = _export_case(tmp_path, report)
    summary = evaluate_cases(
        [
            EvaluationCase(
                case_id="case-1",
                investigation_dir=str(path),
                true_hypothesis_id="a",
                should_abstain=False,
            )
        ]
    )
    assert summary.top1_accuracy == 1.0
    assert summary.top3_accuracy == 1.0
    assert summary.unsupported_evidence_rate == 0.0
    assert summary.evidence_citation_coverage == 1.0


def test_evaluation_rejects_standalone_or_semantically_tampered_export(tmp_path: Path) -> None:
    report = score_proposal(
        InvestigationProposal.model_validate(
            {
                "summary": "A",
                "hypotheses": [
                    {
                        "hypothesis_id": "a",
                        "title": "A",
                        "prior_probability": 0.5,
                        "rationale": "A",
                        "evidence": [
                            {
                                "evidence_record_id": "E1",
                                "direction": "support",
                                "likelihood_ratio": 2,
                                "explanation": "A",
                            }
                        ],
                        "falsifiers": ["not A"],
                    },
                    {
                        "hypothesis_id": "b",
                        "title": "B",
                        "prior_probability": 0.5,
                        "rationale": "B",
                        "evidence": [
                            {
                                "evidence_record_id": "E1",
                                "direction": "contradict",
                                "likelihood_ratio": 0.5,
                                "explanation": "B",
                            }
                        ],
                        "falsifiers": ["not B"],
                    },
                ],
            }
        ),
        investigation_id="eval",
        incident_id="inc",
        question="why",
        policy=__import__("paic.investigation.config", fromlist=["DecisionPolicy"]).DecisionPolicy(
            minimum_distinct_evidence=1
        ),
        observed_evidence={"E1"},
        source_hashes={},
        attempts=[],
        trace=[],
        total_tokens=0,
    )
    standalone = tmp_path / "report.json"
    standalone.write_text(report.model_dump_json(), encoding="utf-8")
    with pytest.raises(InvestigationArtifactError):
        evaluate_cases([EvaluationCase(case_id="standalone", investigation_dir=str(standalone))])
    artifact = _export_case(tmp_path, report)
    report_path = artifact / "report.json"
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    raw["confidence"] = 0.0
    report_path.write_text(json.dumps(raw), encoding="utf-8")
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "report.json":
            content = report_path.read_bytes()
            item["byte_size"] = len(content)
            item["sha256"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (artifact / "_SUCCESS").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n"
    )
    with pytest.raises(InvestigationArtifactError):
        evaluate_cases([EvaluationCase(case_id="tampered", investigation_dir=str(artifact))])


def test_brier_counts_omitted_true_hypothesis(tmp_path: Path) -> None:
    report = score_proposal(
        InvestigationProposal.model_validate(
            {
                "summary": "A",
                "hypotheses": [
                    {
                        "hypothesis_id": "a",
                        "title": "A",
                        "prior_probability": 0.5,
                        "rationale": "A",
                        "evidence": [
                            {
                                "evidence_record_id": "E1",
                                "direction": "support",
                                "likelihood_ratio": 2,
                                "explanation": "A",
                            }
                        ],
                        "falsifiers": ["not A"],
                    },
                    {
                        "hypothesis_id": "b",
                        "title": "B",
                        "prior_probability": 0.5,
                        "rationale": "B",
                        "evidence": [
                            {
                                "evidence_record_id": "E1",
                                "direction": "contradict",
                                "likelihood_ratio": 0.5,
                                "explanation": "B",
                            }
                        ],
                        "falsifiers": ["not B"],
                    },
                ],
            }
        ),
        investigation_id="eval",
        incident_id="inc",
        question="why",
        policy=__import__("paic.investigation.config", fromlist=["DecisionPolicy"]).DecisionPolicy(
            minimum_distinct_evidence=1
        ),
        observed_evidence={"E1"},
        source_hashes={},
        attempts=[],
        trace=[],
        total_tokens=0,
    )
    summary = evaluate_cases(
        [
            EvaluationCase(
                case_id="omitted",
                investigation_dir=str(_export_case(tmp_path, report)),
                true_hypothesis_id="missing",
            )
        ]
    )
    assert summary.mean_brier_score == pytest.approx(
        1.0 + sum(item.posterior_probability**2 for item in report.hypotheses)
    )

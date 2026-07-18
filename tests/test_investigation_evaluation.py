from __future__ import annotations

from pathlib import Path

from paic.investigation.evaluation import EvaluationCase, evaluate_cases
from paic.investigation.models import InvestigationProposal
from paic.investigation.probability import score_proposal


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
    path = tmp_path / "report.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    summary = evaluate_cases(
        [
            EvaluationCase(
                case_id="case-1",
                report_path=str(path),
                true_hypothesis_id="a",
                should_abstain=False,
            )
        ]
    )
    assert summary.top1_accuracy == 1.0
    assert summary.top3_accuracy == 1.0
    assert summary.unsupported_evidence_rate == 0.0
    assert summary.evidence_citation_coverage == 1.0

from __future__ import annotations

import math

import pytest

from paic.investigation.config import DecisionPolicy
from paic.investigation.models import InvestigationProposal
from paic.investigation.probability import ProposalValidationError, score_proposal, verify_report


def _proposal() -> InvestigationProposal:
    return InvestigationProposal.model_validate(
        {
            "summary": "The deployment hypothesis has stronger evidence.",
            "hypotheses": [
                {
                    "hypothesis_id": "deployment",
                    "title": "Regional deployment regression",
                    "prior_probability": 0.5,
                    "rationale": "A relevant change preceded the incident.",
                    "evidence": [
                        {
                            "evidence_record_id": "E1",
                            "direction": "support",
                            "likelihood_ratio": 6.0,
                            "explanation": "Temporal and cohort alignment.",
                        },
                        {
                            "evidence_record_id": "E2",
                            "direction": "support",
                            "likelihood_ratio": 2.0,
                            "explanation": "Healthy downstream payment service.",
                        },
                    ],
                    "falsifiers": ["No recovery after rollback"],
                },
                {
                    "hypothesis_id": "payments",
                    "title": "Payment service degradation",
                    "prior_probability": 0.5,
                    "rationale": "Payments can reduce checkout completion.",
                    "evidence": [
                        {
                            "evidence_record_id": "E2",
                            "direction": "contradict",
                            "likelihood_ratio": 0.2,
                            "explanation": "Payment health remained normal.",
                        }
                    ],
                    "falsifiers": ["Payment service degradation becomes independently observable."],
                },
            ],
            "explicit_unknowns": ["Rollback has not been observed."],
            "recommended_next_steps": ["Compare conversion after a controlled rollback."],
        }
    )


def test_probability_engine_reconstructs_and_abstains_by_policy() -> None:
    report = score_proposal(
        _proposal(),
        investigation_id="test-investigation",
        incident_id="inc-1",
        question="why",
        policy=DecisionPolicy(minimum_distinct_evidence=2),
        observed_evidence={"E1", "E2"},
        source_hashes={"dataset": "a" * 64},
        attempts=[],
        trace=[],
        total_tokens=10,
    )
    expected = 6.0 / (6.0 + 0.1)
    assert report.hypotheses[0].posterior_probability == pytest.approx(expected)
    assert report.status == "concluded"
    verify_report(report, DecisionPolicy(minimum_distinct_evidence=2))
    assert math.isclose(sum(item.posterior_probability for item in report.hypotheses), 1.0)


def test_probability_rejects_unobserved_evidence() -> None:
    with pytest.raises(ProposalValidationError, match="unobserved"):
        score_proposal(
            _proposal(),
            investigation_id="test-investigation",
            incident_id="inc-1",
            question="why",
            policy=DecisionPolicy(),
            observed_evidence={"E1"},
            source_hashes={},
            attempts=[],
            trace=[],
            total_tokens=0,
        )


def test_proposal_priors_must_form_a_probability_distribution() -> None:
    raw = _proposal().model_dump(mode="json")
    raw["hypotheses"][0]["prior_probability"] = 0.7
    with pytest.raises(ValueError, match="sum to 1"):
        InvestigationProposal.model_validate(raw)

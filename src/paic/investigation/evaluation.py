"""Deterministic evaluation metrics for investigation reports."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from paic.investigation.artifact import InvestigationArtifactError, replay_investigation
from paic.investigation.models import InvestigationReport


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=200)
    investigation_dir: str = Field(min_length=1)
    true_hypothesis_id: str | None = None
    should_abstain: bool = False


class EvaluationSummary(StrictModel):
    case_count: int = Field(ge=0)
    top1_accuracy: float = Field(ge=0.0, le=1.0)
    top3_accuracy: float = Field(ge=0.0, le=1.0)
    mean_brier_score: float = Field(ge=0.0)
    abstention_accuracy: float = Field(ge=0.0, le=1.0)
    evidence_citation_coverage: float = Field(ge=0.0, le=1.0)
    unsupported_evidence_rate: float = Field(ge=0.0, le=1.0)


def _load_report(path: str | Path) -> InvestigationReport:
    """Load only a verified, complete investigation export.

    A standalone report is deliberately not a benchmark input: it has no
    manifest, transcript, receipt, or success-marker integrity context.
    """

    root = Path(path)
    if root.name == "report.json" or not root.is_dir():
        raise InvestigationArtifactError("benchmark cases require an investigation_dir export")
    return replay_investigation(root, artifact_only=True)


def evaluate_cases(cases: list[EvaluationCase]) -> EvaluationSummary:
    if not cases:
        return EvaluationSummary(
            case_count=0,
            top1_accuracy=0.0,
            top3_accuracy=0.0,
            mean_brier_score=0.0,
            abstention_accuracy=0.0,
            evidence_citation_coverage=0.0,
            unsupported_evidence_rate=0.0,
        )
    top1 = 0
    top3 = 0
    brier_total = 0.0
    abstention_correct = 0
    hypotheses_total = 0
    cited_hypotheses = 0
    unsupported = 0
    cited = 0
    for case in cases:
        report = _load_report(case.investigation_dir)
        ranked = sorted(
            report.hypotheses,
            key=lambda item: (-item.posterior_probability, item.hypothesis_id),
        )
        if case.true_hypothesis_id is not None:
            if ranked and ranked[0].hypothesis_id == case.true_hypothesis_id:
                top1 += 1
            if case.true_hypothesis_id in {item.hypothesis_id for item in ranked[:3]}:
                top3 += 1
            probabilities = {item.hypothesis_id: item.posterior_probability for item in ranked}
            class_support = set(probabilities)
            class_support.add(case.true_hypothesis_id)
            # This project intentionally reports the sum-style multiclass Brier
            # score. The omitted true class therefore contributes (0 - 1)^2.
            brier_total += sum(
                (
                    probabilities.get(hypothesis_id, 0.0)
                    - (1.0 if hypothesis_id == case.true_hypothesis_id else 0.0)
                )
                ** 2
                for hypothesis_id in class_support
            )
        abstained = report.status == "abstained"
        abstention_correct += int(abstained == case.should_abstain)
        observed = set(report.observed_evidence_record_ids)
        for hypothesis in ranked:
            hypotheses_total += 1
            refs = set(hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids)
            cited_hypotheses += int(bool(refs))
            cited += len(refs)
            unsupported += len(refs.difference(observed))
    denominator = max(1, sum(case.true_hypothesis_id is not None for case in cases))
    return EvaluationSummary(
        case_count=len(cases),
        top1_accuracy=top1 / denominator,
        top3_accuracy=top3 / denominator,
        mean_brier_score=brier_total / denominator,
        abstention_accuracy=abstention_correct / len(cases),
        evidence_citation_coverage=cited_hypotheses / max(1, hypotheses_total),
        unsupported_evidence_rate=unsupported / max(1, cited),
    )

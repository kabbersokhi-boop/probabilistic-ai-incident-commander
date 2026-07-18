"""Deterministic Bayesian-style hypothesis scoring and abstention."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from paic.investigation.config import DecisionPolicy
from paic.investigation.models import (
    ComputedHypothesis,
    InvestigationProposal,
    InvestigationReport,
    ModelAttempt,
    ToolTraceEntry,
)
from paic.tools.ledger import canonical


class ProposalValidationError(ValueError):
    pass


def _report_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def score_proposal(
    proposal: InvestigationProposal,
    *,
    investigation_id: str,
    incident_id: str,
    question: str,
    policy: DecisionPolicy,
    observed_evidence: set[str],
    source_hashes: dict[str, str],
    attempts: list[ModelAttempt],
    trace: list[ToolTraceEntry],
    total_tokens: int,
) -> InvestigationReport:
    referenced = {
        item.evidence_record_id
        for hypothesis in proposal.hypotheses
        for item in hypothesis.evidence
    }
    unknown = sorted(referenced.difference(observed_evidence))
    if unknown:
        raise ProposalValidationError(f"proposal cites unobserved evidence: {unknown[:5]}")
    log_scores: list[float] = []
    for hypothesis in proposal.hypotheses:
        score = math.log(hypothesis.prior_probability)
        for assessment in hypothesis.evidence:
            if (
                not policy.likelihood_ratio_min
                <= assessment.likelihood_ratio
                <= policy.likelihood_ratio_max
            ):
                raise ProposalValidationError("likelihood ratio outside configured bounds")
            score += math.log(assessment.likelihood_ratio)
        log_scores.append(score)
    maximum = max(log_scores)
    weights = [math.exp(value - maximum) for value in log_scores]
    denominator = sum(weights)
    posteriors = [value / denominator for value in weights]
    computed: list[ComputedHypothesis] = []
    for hypothesis, log_score, posterior in zip(
        proposal.hypotheses, log_scores, posteriors, strict=True
    ):
        computed.append(
            ComputedHypothesis(
                hypothesis_id=hypothesis.hypothesis_id,
                title=hypothesis.title,
                prior_probability=hypothesis.prior_probability,
                posterior_probability=posterior,
                log_evidence_score=log_score,
                rationale=hypothesis.rationale,
                supporting_evidence_ids=sorted(
                    item.evidence_record_id
                    for item in hypothesis.evidence
                    if item.direction == "support"
                ),
                contradicting_evidence_ids=sorted(
                    item.evidence_record_id
                    for item in hypothesis.evidence
                    if item.direction == "contradict"
                ),
                falsifiers=hypothesis.falsifiers,
            )
        )
    computed.sort(key=lambda item: (-item.posterior_probability, item.hypothesis_id))
    top = computed[0]
    second = computed[1]
    margin = top.posterior_probability - second.posterior_probability
    if len(computed) <= 1:
        entropy = 0.0
    else:
        entropy = -sum(
            item.posterior_probability * math.log(item.posterior_probability)
            for item in computed
            if item.posterior_probability > 0
        ) / math.log(len(computed))
    distinct_evidence = set(top.supporting_evidence_ids + top.contradicting_evidence_ids)
    concluded = (
        top.posterior_probability >= policy.minimum_top_posterior
        and margin >= policy.minimum_margin
        and len(distinct_evidence) >= policy.minimum_distinct_evidence
        and entropy <= policy.maximum_normalized_entropy
    )
    if not concluded and not proposal.explicit_unknowns:
        raise ProposalValidationError("abstained investigations require an explicit unknown")
    if not concluded and not proposal.recommended_next_steps:
        raise ProposalValidationError("abstained investigations require a read-only next check")
    confidence = max(0.0, min(1.0, top.posterior_probability * (1.0 - entropy)))
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "investigation_id": investigation_id,
        "incident_id": incident_id,
        "question": question,
        "status": "concluded" if concluded else "abstained",
        "summary": proposal.summary,
        "selected_hypothesis_id": top.hypothesis_id if concluded else None,
        "confidence": confidence,
        "normalized_entropy": entropy,
        "posterior_margin": margin,
        "hypotheses": [item.model_dump(mode="json") for item in computed],
        "explicit_unknowns": proposal.explicit_unknowns,
        "recommended_next_steps": proposal.recommended_next_steps,
        "observed_evidence_record_ids": sorted(observed_evidence),
        "source_manifest_hashes": dict(sorted(source_hashes.items())),
        "model_attempts": [item.model_dump(mode="json") for item in attempts],
        "tool_trace": [item.model_dump(mode="json") for item in trace],
        "total_tokens": total_tokens,
        "proposal": proposal.model_dump(mode="json"),
    }
    payload["report_sha256"] = _report_hash(payload)
    return InvestigationReport.model_validate(payload)


def verify_report(report: InvestigationReport, policy: DecisionPolicy) -> None:
    recomputed = score_proposal(
        report.proposal,
        investigation_id=report.investigation_id,
        incident_id=report.incident_id,
        question=report.question,
        policy=policy,
        observed_evidence=set(report.observed_evidence_record_ids),
        source_hashes=report.source_manifest_hashes,
        attempts=report.model_attempts,
        trace=report.tool_trace,
        total_tokens=report.total_tokens,
    )
    if recomputed != report:
        raise ProposalValidationError("investigation report does not reconstruct")

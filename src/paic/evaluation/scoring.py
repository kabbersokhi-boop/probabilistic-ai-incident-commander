"""Deterministic benchmark scoring; no model or provider calls occur here."""

from __future__ import annotations

from paic.evaluation.models import (
    AggregateMetrics,
    CaseResult,
    HiddenAnswerKey,
    Prediction,
    VisibleCase,
)


def score_case(case: VisibleCase, answer: HiddenAnswerKey, prediction: Prediction) -> CaseResult:
    if case.case_id != answer.case_id or case.case_id != prediction.case_id:
        raise ValueError("case, answer, and prediction IDs must match")
    acceptable = {answer.root_cause_id, *answer.acceptable_alternates}
    ranked = prediction.ranked_hypotheses
    position = next((index + 1 for index, item in enumerate(ranked) if item in acceptable), None)
    observed = set(case.evidence_ids)
    required = set(answer.required_evidence_ids)
    cited = set(prediction.cited_evidence_ids)
    valid_citations = cited.issubset(observed)
    brier = sum(
        (prediction.probabilities.get(item, 0.0) - float(item == answer.root_cause_id)) ** 2
        for item in prediction.ranked_hypotheses
    )
    return CaseResult(
        case_id=case.case_id,
        top1_correct=position == 1,
        top3_correct=position is not None and position <= 3,
        reciprocal_rank=0.0 if position is None else 1.0 / position,
        brier_score=brier,
        abstention_correct=prediction.abstained == answer.should_abstain,
        required_evidence_coverage=(
            1.0 if not required else len(required.intersection(cited)) / len(required)
        ),
        unsupported_claim_count=len(set(prediction.claims).intersection(answer.prohibited_claims)),
        cited_evidence_valid=valid_citations,
        tool_calls=prediction.tool_calls,
    )


def expected_calibration_error(
    answers: list[HiddenAnswerKey], predictions: list[Prediction], bins: int = 10
) -> float:
    if len(answers) != len(predictions):
        raise ValueError("answers and predictions must have equal length")
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for answer, prediction in zip(answers, predictions, strict=True):
        confidence = max(prediction.probabilities.values())
        correct = prediction.ranked_hypotheses[0] == answer.root_cause_id
        buckets[min(bins - 1, int(confidence * bins))].append((confidence, correct))
    total = max(1, len(predictions))
    return sum(
        len(bucket)
        / total
        * abs(
            sum(confidence for confidence, _ in bucket) / len(bucket)
            - sum(correct for _, correct in bucket) / len(bucket)
        )
        for bucket in buckets
        if bucket
    )


def aggregate_results(
    results: list[CaseResult], answers: list[HiddenAnswerKey], predictions: list[Prediction]
) -> AggregateMetrics:
    count = len(results)
    if not count or len(answers) != count or len(predictions) != count:
        raise ValueError("benchmark result lengths must match and be non-zero")
    selective = [
        result.top1_correct
        for result, prediction in zip(results, predictions, strict=True)
        if not prediction.abstained
    ]
    unsafe = sum(result.unsupported_claim_count for result in results)
    return AggregateMetrics(
        case_count=count,
        top1_accuracy=sum(result.top1_correct for result in results) / count,
        top3_recall=sum(result.top3_correct for result in results) / count,
        mean_reciprocal_rank=sum(result.reciprocal_rank for result in results) / count,
        brier_score=sum(result.brier_score for result in results) / count,
        expected_calibration_error=expected_calibration_error(answers, predictions),
        abstention_accuracy=sum(result.abstention_correct for result in results) / count,
        selective_accuracy=(sum(selective) / len(selective) if selective else 0.0),
        required_evidence_coverage=sum(result.required_evidence_coverage for result in results)
        / count,
        unsupported_claim_count=unsafe,
        safety_passed=unsafe == 0 and all(result.cited_evidence_valid for result in results),
        mean_tool_calls=sum(result.tool_calls for result in results) / count,
    )

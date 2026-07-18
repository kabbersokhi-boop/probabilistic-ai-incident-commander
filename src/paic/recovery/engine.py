"""Deterministic recovery evaluation over primary and guardrail metrics."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from paic.recovery.config import RecoveryConfig, RecoveryMetricPolicy
from paic.recovery.models import (
    RecoveryMetricEvaluation,
    RecoveryObservation,
    RecoveryObservationSet,
    RecoveryReport,
)
from paic.recovery.statistics import robust_center_scale, theil_sen_slope, welch_tost


class RecoveryEvaluationError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, float):
            if not math.isfinite(item):
                raise RecoveryEvaluationError(
                    "canonical recovery payload contains a non-finite value"
                )
            return 0.0 if item == 0.0 else item
        if isinstance(item, dict):
            return {str(key): normalize(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(nested) for nested in item]
        return item

    return json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def report_digest(report: RecoveryReport) -> str:
    payload = report.model_dump(mode="json")
    payload.pop("report_sha256", None)
    return digest(payload)


def verify_report(report: RecoveryReport) -> None:
    if report.report_sha256 != report_digest(report):
        raise RecoveryEvaluationError("recovery report hash mismatch")


def _adverse(value: float, baseline: float, policy: RecoveryMetricPolicy) -> float:
    if policy.healthy_direction == "higher_is_better":
        return max(0.0, baseline - value)
    if policy.healthy_direction == "lower_is_better":
        return max(0.0, value - baseline)
    return abs(value - baseline)


def _evaluate_metric(
    policy: RecoveryMetricPolicy,
    observations: list[RecoveryObservation],
    *,
    executed_at: datetime,
    evaluated_at: datetime,
    alpha: float,
    scale_floor: float,
) -> RecoveryMetricEvaluation:
    eligible = [item for item in observations if item.sample_size >= policy.minimum_sample_size]
    baseline_obs = [item for item in eligible if item.observed_at < executed_at]
    post_obs = [item for item in eligible if executed_at <= item.observed_at <= evaluated_at]
    baseline_obs = sorted(baseline_obs, key=lambda item: item.observed_at)[
        -policy.baseline_lookback_periods :
    ]
    post_obs = sorted(post_obs, key=lambda item: item.observed_at)
    sustain_obs = post_obs[-policy.sustain_periods :]
    reason_codes: list[str] = []
    if len(baseline_obs) < policy.minimum_baseline_periods:
        reason_codes.append("insufficient_baseline")
    if len(post_obs) < policy.minimum_post_periods:
        reason_codes.append("insufficient_post_window")
    if len(sustain_obs) < policy.sustain_periods:
        reason_codes.append("insufficient_sustain_window")
    if reason_codes:
        return RecoveryMetricEvaluation(
            metric_id=policy.metric_id,
            cohort=policy.cohort,
            role=policy.role,
            healthy_direction=policy.healthy_direction,
            status="insufficient_data",
            baseline_count=len(baseline_obs),
            post_count=len(post_obs),
            sustain_count=len(sustain_obs),
            baseline_center=None,
            baseline_scale=None,
            latest_center=None,
            equivalence_margin=None,
            equivalence_pvalue=None,
            within_band_fraction=None,
            latest_robust_z=None,
            distance_slope=None,
            improvement_fraction=None,
            severe_breach=False,
            reason_codes=reason_codes,
        )

    baseline_values = [item.value for item in baseline_obs]
    post_values = [item.value for item in post_obs]
    sustain_values = [item.value for item in sustain_obs]
    baseline_center, baseline_scale = robust_center_scale(baseline_values, floor=scale_floor)
    latest_center, _ = robust_center_scale(sustain_values, floor=scale_floor)
    margin = max(
        abs(baseline_center) * policy.equivalence_margin_relative,
        policy.equivalence_margin_absolute,
        scale_floor,
    )
    equivalence = welch_tost(baseline_values, sustain_values, margin=margin, alpha=alpha)
    within_fraction = sum(abs(value - baseline_center) <= margin for value in sustain_values) / len(
        sustain_values
    )
    latest_z = (latest_center - baseline_center) / baseline_scale
    adverse_distances = [_adverse(value, baseline_center, policy) for value in post_values]
    distance_slope = theil_sen_slope(adverse_distances)
    first_distance = adverse_distances[0]
    latest_distance = _adverse(latest_center, baseline_center, policy)
    improvement = (first_distance - latest_distance) / max(first_distance, margin, scale_floor)
    severe = latest_distance > policy.severe_robust_z * baseline_scale
    slope_limit = margin / max(policy.sustain_periods, 1)
    regression = distance_slope > slope_limit

    if not equivalence.equivalent:
        reason_codes.append("not_statistically_equivalent")
    if within_fraction < policy.minimum_within_band_fraction:
        reason_codes.append("sustain_window_outside_band")
    if regression:
        reason_codes.append("adverse_distance_trend")
    if severe:
        reason_codes.append(
            "severe_guardrail_breach" if policy.role == "guardrail" else "severe_primary_gap"
        )

    recovered = (
        equivalence.equivalent
        and within_fraction >= policy.minimum_within_band_fraction
        and not regression
        and not severe
    )
    if recovered:
        status: Literal["recovered", "recovering", "failed"] = "recovered"
        reason_codes = ["statistically_recovered"]
    elif improvement >= policy.minimum_improvement_fraction and not severe:
        status = "recovering"
        reason_codes.append("meaningful_improvement")
    else:
        status = "failed"
        reason_codes.append("recovery_gate_failed")

    return RecoveryMetricEvaluation(
        metric_id=policy.metric_id,
        cohort=policy.cohort,
        role=policy.role,
        healthy_direction=policy.healthy_direction,
        status=status,
        baseline_count=len(baseline_obs),
        post_count=len(post_obs),
        sustain_count=len(sustain_obs),
        baseline_center=baseline_center,
        baseline_scale=baseline_scale,
        latest_center=latest_center,
        equivalence_margin=margin,
        equivalence_pvalue=equivalence.pvalue,
        within_band_fraction=within_fraction,
        latest_robust_z=latest_z,
        distance_slope=distance_slope,
        improvement_fraction=improvement,
        severe_breach=severe and policy.role == "guardrail",
        reason_codes=list(dict.fromkeys(reason_codes)),
    )


def evaluate_recovery(
    config: RecoveryConfig,
    observation_set: RecoveryObservationSet,
    *,
    execution_manifest_sha256: str,
    evaluated_at: datetime | None = None,
) -> RecoveryReport:
    if config.incident_id != observation_set.incident_id:
        raise RecoveryEvaluationError(
            "recovery configuration and observations target different incidents"
        )
    if execution_manifest_sha256 != observation_set.execution_manifest_sha256:
        raise RecoveryEvaluationError("observations are bound to another execution manifest")
    at = evaluated_at or observation_set.generated_at
    if at.tzinfo is None or at.utcoffset() is None:
        raise RecoveryEvaluationError("evaluation time must include a timezone offset")
    if at < observation_set.executed_at:
        raise RecoveryEvaluationError("recovery cannot be evaluated before remediation execution")

    grouped: dict[tuple[str, str], list[RecoveryObservation]] = defaultdict(list)
    for item in observation_set.observations:
        grouped[(item.metric_id, item.cohort)].append(item)
    configured = {(item.metric_id, item.cohort) for item in config.metrics}
    undeclared = set(grouped).difference(configured)
    if undeclared:
        raise RecoveryEvaluationError(
            f"observations contain undeclared recovery series: {sorted(undeclared)}"
        )

    evaluations = [
        _evaluate_metric(
            policy,
            grouped.get((policy.metric_id, policy.cohort), []),
            executed_at=observation_set.executed_at,
            evaluated_at=at,
            alpha=config.alpha,
            scale_floor=config.scale_floor,
        )
        for policy in config.metrics
    ]
    primary = [item for item in evaluations if item.role == "primary"]
    guardrails = [item for item in evaluations if item.role == "guardrail"]
    severe = any(item.severe_breach for item in guardrails)
    any_insufficient = any(item.status == "insufficient_data" for item in evaluations)
    all_primary = all(item.status == "recovered" for item in primary)
    all_guardrails = all(item.status == "recovered" for item in guardrails)
    any_failed = any(item.status == "failed" for item in evaluations)
    if any_insufficient:
        decision = "insufficient_data"
    elif all_primary and all_guardrails:
        decision = "recovered"
    elif severe or any_failed:
        decision = "failed"
    else:
        decision = "recovering"

    payload = {
        "schema_version": "1.0",
        "recovery_id": config.recovery_id,
        "incident_id": config.incident_id,
        "observation_set_id": observation_set.observation_set_id,
        "execution_receipt_sha256": observation_set.execution_receipt_sha256,
        "execution_manifest_sha256": execution_manifest_sha256,
        "config_sha256": digest(config.model_dump(mode="json")),
        "observation_set_sha256": digest(observation_set.model_dump(mode="json")),
        "evaluated_at": at,
        "decision": decision,
        "primary_recovered": sum(item.status == "recovered" for item in primary),
        "primary_total": len(primary),
        "guardrails_healthy": sum(item.status == "recovered" for item in guardrails),
        "guardrail_total": len(guardrails),
        "severe_guardrail_breach": severe,
        "metric_evaluations": evaluations,
    }
    provisional = RecoveryReport.model_validate({**payload, "report_sha256": "0" * 64})
    report = provisional.model_copy(update={"report_sha256": report_digest(provisional)})
    verify_report(report)
    return report

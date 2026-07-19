from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paic.recovery.config import RecoveryConfig, RecoveryConfigError, load_recovery_config
from paic.recovery.engine import (
    RecoveryEvaluationError,
    _adverse,
    canonical,
    evaluate_recovery,
    verify_report,
)
from paic.recovery.models import RecoveryObservationSet, RecoveryReport
from paic.recovery.statistics import robust_center_scale, welch_tost


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def config(*, reopen: int = 2) -> RecoveryConfig:
    return RecoveryConfig.model_validate(
        {
            "schema_version": "1.0",
            "recovery_id": "recovery-smoke",
            "incident_id": "incident-smoke",
            "alpha": 0.05,
            "scale_floor": 0.0001,
            "reopen_after_consecutive_failures": reopen,
            "metrics": [
                {
                    "metric_id": "conversion",
                    "cohort": "android-eu",
                    "role": "primary",
                    "healthy_direction": "higher_is_better",
                    "baseline_lookback_periods": 8,
                    "minimum_baseline_periods": 6,
                    "minimum_post_periods": 4,
                    "sustain_periods": 3,
                    "minimum_sample_size": 20,
                    "equivalence_margin_relative": 0.05,
                    "equivalence_margin_absolute": 0.02,
                    "minimum_within_band_fraction": 0.66,
                    "minimum_improvement_fraction": 0.4,
                    "severe_robust_z": 5.0,
                },
                {
                    "metric_id": "payment-approval",
                    "cohort": "android-eu",
                    "role": "guardrail",
                    "healthy_direction": "higher_is_better",
                    "baseline_lookback_periods": 8,
                    "minimum_baseline_periods": 6,
                    "minimum_post_periods": 4,
                    "sustain_periods": 3,
                    "minimum_sample_size": 20,
                    "equivalence_margin_relative": 0.03,
                    "equivalence_margin_absolute": 0.01,
                    "minimum_within_band_fraction": 0.66,
                    "minimum_improvement_fraction": 0.4,
                    "severe_robust_z": 4.0,
                },
            ],
        }
    )


def observations(
    *,
    healthy: bool = True,
    generated_hours: int = 6,
    include_guardrail: bool = True,
) -> RecoveryObservationSet:
    executed = datetime(2026, 1, 1, 12, tzinfo=UTC)
    base_conversion = [0.72, 0.719, 0.721, 0.720, 0.718, 0.722]
    base_payment = [0.96, 0.961, 0.959, 0.960, 0.962, 0.960]
    post_conversion = (
        [0.64, 0.69, 0.716, 0.720, 0.721] if healthy else [0.72, 0.69, 0.64, 0.60, 0.58]
    )
    post_payment = (
        [0.958, 0.960, 0.961, 0.960, 0.961] if healthy else [0.960, 0.940, 0.88, 0.82, 0.78]
    )
    rows: list[dict[str, object]] = []
    for metric, values in (("conversion", base_conversion), ("payment-approval", base_payment)):
        if metric == "payment-approval" and not include_guardrail:
            continue
        for index, value in enumerate(values):
            rows.append(
                {
                    "metric_id": metric,
                    "cohort": "android-eu",
                    "observed_at": executed - timedelta(hours=len(values) - index),
                    "value": value,
                    "sample_size": 100,
                }
            )
    for metric, values in (("conversion", post_conversion), ("payment-approval", post_payment)):
        if metric == "payment-approval" and not include_guardrail:
            continue
        for index, value in enumerate(values):
            rows.append(
                {
                    "metric_id": metric,
                    "cohort": "android-eu",
                    "observed_at": executed + timedelta(hours=index + 1),
                    "value": value,
                    "sample_size": 100,
                }
            )
    return RecoveryObservationSet.model_validate(
        {
            "observation_set_id": f"set-{'healthy' if healthy else 'regression'}-{generated_hours}",
            "incident_id": "incident-smoke",
            "execution_receipt_sha256": sha("receipt"),
            "execution_manifest_sha256": sha("execution-manifest"),
            "analytics_manifest_sha256": sha("analytics-manifest"),
            "source_simulation_id": "simulation-smoke",
            "generator_config_sha256": sha("observation-scenario"),
            "executed_at": executed,
            "generated_at": executed + timedelta(hours=generated_hours),
            "observations": rows,
        }
    )


def report(healthy: bool = True, generated_hours: int = 6) -> RecoveryReport:
    return evaluate_recovery(
        config(),
        observations(healthy=healthy, generated_hours=generated_hours),
        execution_manifest_sha256=sha("execution-manifest"),
    )


def test_robust_statistics_and_equivalence() -> None:
    center, scale = robust_center_scale([1.0, 1.0, 1.01, 0.99], floor=0.001)
    assert center == 1.0
    assert scale >= 0.001
    result = welch_tost([1.0, 1.01, 0.99, 1.0], [1.0, 1.0, 1.01, 0.99], margin=0.05, alpha=0.05)
    assert result.equivalent
    assert result.pvalue < 0.05


def test_canonical_recovery_payload_normalizes_negative_zero() -> None:
    assert canonical({"slope": -0.0}) == canonical({"slope": 0.0})


def test_healthy_windows_are_recovered() -> None:
    result = report(True)
    assert result.decision == "recovered"
    assert result.primary_recovered == result.primary_total == 1
    assert result.guardrails_healthy == result.guardrail_total == 1
    assert not result.severe_guardrail_breach
    assert all(item.status == "recovered" for item in result.metric_evaluations)
    verify_report(result)


def test_regression_fails_and_marks_severe_guardrail() -> None:
    result = report(False)
    assert result.decision == "failed"
    assert result.severe_guardrail_breach
    guardrail = next(item for item in result.metric_evaluations if item.role == "guardrail")
    assert guardrail.status == "failed"
    assert guardrail.severe_breach


def test_missing_series_is_insufficient_data() -> None:
    result = evaluate_recovery(
        config(),
        observations(include_guardrail=False),
        execution_manifest_sha256=sha("execution-manifest"),
    )
    assert result.decision == "insufficient_data"
    guardrail = next(item for item in result.metric_evaluations if item.role == "guardrail")
    assert guardrail.reason_codes == [
        "insufficient_baseline",
        "insufficient_post_window",
        "insufficient_sustain_window",
    ]


def test_failed_metric_dominates_unrelated_insufficient_data() -> None:
    result = evaluate_recovery(
        config(),
        observations(healthy=False, include_guardrail=False),
        execution_manifest_sha256=sha("execution-manifest"),
    )
    assert result.decision == "failed"
    assert {item.status for item in result.metric_evaluations} == {"failed", "insufficient_data"}


def test_undeclared_series_is_rejected() -> None:
    value = observations().model_dump(mode="json")
    extra = dict(value["observations"][0])
    extra["metric_id"] = "undeclared"
    value["observations"].append(extra)
    with pytest.raises(RecoveryEvaluationError, match="undeclared"):
        evaluate_recovery(
            config(),
            RecoveryObservationSet.model_validate(value),
            execution_manifest_sha256=sha("execution-manifest"),
        )


def test_report_hash_detects_tampering() -> None:
    from paic.recovery.models import RecoveryReport

    value = report().model_dump(mode="json")
    value["decision"] = "failed"
    tampered = RecoveryReport.model_validate(value)
    with pytest.raises(RecoveryEvaluationError, match="hash mismatch"):
        verify_report(tampered)


def test_recovery_config_rejects_duplicate_metrics_and_impossible_windows() -> None:
    value = config().model_dump()
    value["metrics"].append(value["metrics"][0])
    with pytest.raises(ValueError, match="unique"):
        RecoveryConfig.model_validate(value)
    policy = value["metrics"][0].copy()
    policy["sustain_periods"] = 5
    policy["minimum_post_periods"] = 4
    value["metrics"] = [policy]
    with pytest.raises(ValueError, match="sustain"):
        RecoveryConfig.model_validate(value)


def test_recovery_config_loader_reports_missing_and_invalid_yaml(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(config().model_dump_json(), encoding="utf-8")
    assert load_recovery_config(valid) == config()
    with pytest.raises(RecoveryConfigError, match="cannot read"):
        load_recovery_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("metrics: [", encoding="utf-8")
    with pytest.raises(RecoveryConfigError, match="invalid YAML"):
        load_recovery_config(invalid)


def test_statistics_rejects_empty_and_small_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        robust_center_scale([], floor=0.1)
    with pytest.raises(ValueError, match="at least two"):
        welch_tost([1.0], [1.0, 1.0], margin=0.1, alpha=0.05)
    with pytest.raises(ValueError, match="positive"):
        welch_tost([1.0, 1.0], [1.0, 1.0], margin=0.0, alpha=0.05)
    assert welch_tost([1.0, 1.0], [1.0, 1.0], margin=0.1, alpha=0.05).equivalent


def test_recovering_state_and_directional_distance_rules(tmp_path: Path) -> None:
    value = config().model_dump()
    value["metrics"][0]["equivalence_margin_relative"] = 0.000001
    value["metrics"][0]["equivalence_margin_absolute"] = 0.000001
    recovering = evaluate_recovery(
        RecoveryConfig.model_validate(value),
        observations(),
        execution_manifest_sha256=sha("execution-manifest"),
    )
    assert recovering.decision == "recovering"
    lower = config().metrics[0].model_copy(update={"healthy_direction": "lower_is_better"})
    target = config().metrics[0].model_copy(update={"healthy_direction": "target"})
    assert _adverse(2.0, 1.0, lower) == 1.0
    assert _adverse(2.0, 1.0, target) == 1.0

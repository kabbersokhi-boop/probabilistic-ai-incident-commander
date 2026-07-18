from __future__ import annotations

import math

import pytest

from paic.analytics.registry import METRIC_REGISTRY
from paic.detection.statistics import (
    benjamini_hochberg,
    beta_binomial_predictive_two_sided_p,
    count_predictive_p_value,
    distribution_p_value,
    evidence_score,
    exact_binomial_two_sided_p,
    log_student_t_predictive_p,
    robust_interval,
    robust_location_scale,
    severity_from_score,
    student_t_two_sided_p,
)


def test_robust_location_ignores_single_extreme_outlier() -> None:
    location, scale = robust_location_scale(
        [10.0, 10.0, 10.2, 9.8, 10.1, 500.0],
        minimum_scale=0.01,
        relative_scale_floor=0.01,
    )
    assert location == pytest.approx(10.05)
    assert 0.1 <= scale < 1.0


def test_robust_scale_has_deterministic_floor_and_rejects_empty_history() -> None:
    location, scale = robust_location_scale(
        [100.0] * 6, minimum_scale=0.01, relative_scale_floor=0.02
    )
    assert location == 100.0
    assert scale == 2.0
    with pytest.raises(ValueError, match="finite value"):
        robust_location_scale([], minimum_scale=0.01, relative_scale_floor=0.0)


def test_interval_respects_metric_bounds() -> None:
    lower, upper = robust_interval(
        0.95,
        0.10,
        METRIC_REGISTRY["payment_approval_rate"],
    )
    assert lower == pytest.approx(0.70)
    assert upper == 1.0


def test_student_and_exact_binomial_probabilities_are_bounded() -> None:
    assert student_t_two_sided_p(0.0, 10) == pytest.approx(1.0)
    assert student_t_two_sided_p(math.inf, 10) == 1.0
    assert exact_binomial_two_sided_p(0, 10, 0.9) < 1e-6
    assert exact_binomial_two_sided_p(12, 10, 0.5) == 1.0


def test_empirical_beta_binomial_avoids_small_cohort_overconfidence() -> None:
    exact = exact_binomial_two_sided_p(8, 10, 0.99)
    predictive = beta_binomial_predictive_two_sided_p(
        8,
        10,
        history_successes=[5.0] * 10,
        history_trials=[5.0] * 10,
    )
    assert predictive > exact
    assert 0.0 < predictive < 0.05


def test_beta_binomial_handles_invalid_or_missing_history() -> None:
    assert beta_binomial_predictive_two_sided_p(1, 0, [1], [1]) == 1.0
    assert beta_binomial_predictive_two_sided_p(1, 2, [], []) == 1.0
    assert beta_binomial_predictive_two_sided_p(1, 2, [2, -1], [1, 1]) == 1.0


def test_count_model_selects_poisson_or_negative_binomial() -> None:
    name, p_value = count_predictive_p_value(20, [10, 11, 9, 10, 10, 9, 11])
    assert name == "poisson"
    assert p_value < 0.01

    name, p_value = count_predictive_p_value(20, [2, 20, 3, 19, 4, 18, 5])
    assert name == "negative-binomial"
    assert 0.0 <= p_value <= 1.0

    assert count_predictive_p_value(1, [0, 0, 0]) == ("degenerate-count", 0.0)
    assert count_predictive_p_value(-1, [1, 2]) == ("count-unavailable", 1.0)


def test_log_student_predictive_test_handles_skew_and_invalid_values() -> None:
    history = [90.0, 100.0, 110.0, 120.0, 95.0, 105.0, 115.0]
    assert log_student_t_predictive_p(1000.0, history) < 0.05
    assert log_student_t_predictive_p(-1.0, history) == 1.0
    assert log_student_t_predictive_p(10.0, []) == 1.0


def test_distribution_router_uses_metric_appropriate_models() -> None:
    ratio_name, ratio_p = distribution_p_value(
        METRIC_REGISTRY["checkout_conversion_rate"],
        observed=0.5,
        numerator=5.0,
        denominator=10.0,
        robust_z=-4.0,
        history_values=[0.9] * 8,
        history_numerators=[9.0] * 8,
        history_denominators=[10.0] * 8,
    )
    assert ratio_name == "empirical-beta-binomial"
    assert ratio_p < 0.05

    count_name, _ = distribution_p_value(
        METRIC_REGISTRY["completed_orders"],
        observed=30.0,
        numerator=30.0,
        denominator=None,
        robust_z=4.0,
        history_values=[10.0, 11.0, 9.0, 10.0],
        history_numerators=[],
        history_denominators=[],
    )
    assert count_name in {"poisson", "negative-binomial"}

    currency_name, _ = distribution_p_value(
        METRIC_REGISTRY["gross_order_value"],
        observed=2000.0,
        numerator=2000.0,
        denominator=None,
        robust_z=5.0,
        history_values=[100.0, 110.0, 90.0, 105.0],
        history_numerators=[],
        history_denominators=[],
    )
    assert currency_name == "robust-log-student-t"


def test_benjamini_hochberg_is_monotone_and_never_below_p() -> None:
    p_values = [0.01, 0.04, 0.03, 0.20, math.nan, -1.0, 2.0]
    q_values = benjamini_hochberg(p_values)
    assert len(q_values) == len(p_values)
    cleaned = [0.01, 0.04, 0.03, 0.20, 1.0, 0.0, 1.0]
    assert all(q >= p for p, q in zip(cleaned, q_values, strict=True))
    ordered = sorted(zip(cleaned, q_values, strict=True))
    assert [item[1] for item in ordered] == sorted(item[1] for item in ordered)
    assert benjamini_hochberg([]) == []


def test_evidence_score_and_severity_are_bounded() -> None:
    score = evidence_score(
        robust_z=8.0,
        q_value=1e-10,
        cusum_score=10.0,
        cusum_threshold=5.0,
        sequential_log_likelihood=10.0,
        sequential_threshold=4.0,
        relative_change=1.0,
        minimum_relative_effect=0.1,
    )
    assert 0.85 <= score <= 1.0
    assert severity_from_score(score, 4) == "critical"
    assert severity_from_score(0.7, 2) == "high"
    assert severity_from_score(0.5, 1) == "medium"
    assert severity_from_score(0.2, 1) == "low"

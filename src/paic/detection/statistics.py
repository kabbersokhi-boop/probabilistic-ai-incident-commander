"""Distribution-aware statistical primitives for anomaly detection."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
from scipy import stats

from paic.analytics.registry import MetricDefinition

_EPSILON = 1e-12


def robust_location_scale(
    values: Sequence[float], *, minimum_scale: float, relative_scale_floor: float
) -> tuple[float, float]:
    """Median and MAD scale with IQR/std fallbacks and a relative floor."""

    array: npt.NDArray[np.float64] = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        raise ValueError("robust baseline requires at least one finite value")
    location = float(np.median(array))
    mad = float(np.median(np.abs(array - location)))
    scale = 1.4826 * mad
    if scale <= _EPSILON and array.size >= 4:
        quantiles: npt.NDArray[np.float64] = np.asarray(
            np.quantile(array, [0.25, 0.75]), dtype=np.float64
        )
        scale = float((quantiles[1] - quantiles[0]) / 1.349)
    if scale <= _EPSILON and array.size >= 2:
        scale = float(np.std(array, ddof=1))
    floor = max(minimum_scale, abs(location) * relative_scale_floor)
    return location, max(scale, floor)


def robust_interval(
    location: float, scale: float, metric: MetricDefinition, *, multiplier: float = 2.5
) -> tuple[float, float]:
    lower = location - multiplier * scale
    upper = location + multiplier * scale
    if metric.expected_min is not None:
        lower = max(lower, metric.expected_min)
    if metric.expected_max is not None:
        upper = min(upper, metric.expected_max)
    return lower, upper


def student_t_two_sided_p(z_score: float, degrees_of_freedom: int) -> float:
    if not math.isfinite(z_score):
        return 1.0
    value = 2.0 * float(stats.t.sf(abs(z_score), df=max(degrees_of_freedom, 1)))
    return min(max(value, 0.0), 1.0)


def exact_binomial_two_sided_p(successes: float, trials: float, probability: float) -> float:
    """Exact probability-ordered two-sided binomial p-value."""

    n = round(trials)
    k = round(successes)
    if n <= 0 or k < 0 or k > n or not math.isfinite(probability):
        return 1.0
    p = min(max(probability, _EPSILON), 1.0 - _EPSILON)
    support = np.arange(n + 1)
    probabilities = stats.binom.pmf(support, n, p)
    observed_probability = float(probabilities[k])
    result = float(probabilities[probabilities <= observed_probability + 1e-15].sum())
    return min(max(result, 0.0), 1.0)


def beta_binomial_predictive_two_sided_p(
    successes: float,
    trials: float,
    history_successes: Sequence[float],
    history_trials: Sequence[float],
) -> float:
    """Empirical-Bayes beta-binomial predictive p-value for a new proportion.

    Historical between-period variation informs the beta prior. The prior
    concentration is capped so a small cohort is not assigned near-infinite
    certainty after several perfect observations.
    """

    n = round(trials)
    k = round(successes)
    if n <= 0 or k < 0 or k > n:
        return 1.0
    valid: list[tuple[float, float]] = []
    for historical_successes, historical_trials in zip(
        history_successes, history_trials, strict=False
    ):
        h_n = float(historical_trials)
        h_k = float(historical_successes)
        if math.isfinite(h_n) and math.isfinite(h_k) and h_n > 0 and 0 <= h_k <= h_n:
            valid.append((h_k, h_n))
    if not valid:
        return 1.0

    total_successes = sum(item[0] for item in valid)
    total_trials = sum(item[1] for item in valid)
    mean_probability = (total_successes + 0.5) / (total_trials + 1.0)
    mean_probability = min(max(mean_probability, 1e-6), 1.0 - 1e-6)

    rates: npt.NDArray[np.float64] = np.asarray(
        [item[0] / item[1] for item in valid], dtype=np.float64
    )
    weights: npt.NDArray[np.float64] = np.asarray([item[1] for item in valid], dtype=np.float64)
    weights /= weights.sum()
    observed_variance = float(np.sum(weights * np.square(rates - mean_probability)))
    denominators: npt.NDArray[np.float64] = np.asarray(
        [item[1] for item in valid], dtype=np.float64
    )
    sampling_variance = float(
        np.sum(weights * mean_probability * (1.0 - mean_probability) / denominators)
    )
    excess_variance = max(observed_variance - sampling_variance, 0.0)
    probability_variance = mean_probability * (1.0 - mean_probability)
    if excess_variance > _EPSILON and probability_variance > _EPSILON:
        rho = min(max(excess_variance / probability_variance, 1e-6), 0.5)
        concentration = 1.0 / rho - 1.0
    else:
        concentration = total_trials + 1.0
    concentration = min(max(concentration, 2.0), 200.0)
    alpha = max(mean_probability * concentration, 0.5)
    beta = max((1.0 - mean_probability) * concentration, 0.5)

    support = np.arange(n + 1)
    probabilities = stats.betabinom.pmf(support, n, alpha, beta)
    observed_probability = float(probabilities[k])
    result = float(probabilities[probabilities <= observed_probability + 1e-15].sum())
    return min(max(result, 0.0), 1.0)


def count_predictive_p_value(observed: float, history: Sequence[float]) -> tuple[str, float]:
    """Poisson or method-of-moments negative-binomial predictive p-value."""

    array: npt.NDArray[np.float64] = np.asarray(history, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0 or not math.isfinite(observed) or observed < 0:
        return "count-unavailable", 1.0
    mean = float(np.mean(array))
    variance = float(np.var(array, ddof=1)) if array.size >= 2 else mean
    x = round(observed)
    if mean <= _EPSILON:
        return "degenerate-count", 1.0 if x == 0 else 0.0
    if variance > mean + max(1e-9, 0.05 * mean):
        shape = mean * mean / (variance - mean)
        probability = shape / (shape + mean)
        support = np.arange(max(x, math.ceil(mean + 10 * math.sqrt(variance))) + 1)
        probabilities = stats.nbinom.pmf(support, shape, probability)
        observed_probability = float(stats.nbinom.pmf(x, shape, probability))
        result = float(probabilities[probabilities <= observed_probability + 1e-15].sum())
        return "negative-binomial", min(max(result, 0.0), 1.0)
    support = np.arange(max(x, math.ceil(mean + 10 * math.sqrt(mean))) + 1)
    probabilities = stats.poisson.pmf(support, mean)
    observed_probability = float(stats.poisson.pmf(x, mean))
    result = float(probabilities[probabilities <= observed_probability + 1e-15].sum())
    return "poisson", min(max(result, 0.0), 1.0)


def log_student_t_predictive_p(observed: float, history: Sequence[float]) -> float:
    """Robust predictive p-value on log1p scale for positive skewed metrics."""

    if not math.isfinite(observed) or observed < 0:
        return 1.0
    transformed = [math.log1p(value) for value in history if math.isfinite(value) and value >= 0]
    if not transformed:
        return 1.0
    location, scale = robust_location_scale(
        transformed, minimum_scale=1e-6, relative_scale_floor=0.01
    )
    z_score = (math.log1p(observed) - location) / scale
    return student_t_two_sided_p(z_score, len(transformed) - 1)


def distribution_p_value(
    definition: MetricDefinition,
    *,
    observed: float,
    numerator: float | None,
    denominator: float | None,
    robust_z: float,
    history_values: Sequence[float],
    history_numerators: Sequence[float],
    history_denominators: Sequence[float],
) -> tuple[str, float]:
    if definition.metric_type == "ratio" and numerator is not None and denominator is not None:
        return (
            "empirical-beta-binomial",
            beta_binomial_predictive_two_sided_p(
                numerator, denominator, history_numerators, history_denominators
            ),
        )
    if definition.metric_type == "count" and definition.calculation in {
        "count",
        "distinct_count",
    }:
        return count_predictive_p_value(observed, history_values)
    if definition.metric_type in {"currency", "duration"}:
        return "robust-log-student-t", log_student_t_predictive_p(observed, history_values)
    return "robust-student-t", student_t_two_sided_p(robust_z, len(history_values) - 1)


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Monotone Benjamini-Hochberg adjusted p-values."""

    count = len(p_values)
    if count == 0:
        return []
    cleaned = [
        min(max(float(value), 0.0), 1.0) if math.isfinite(value) else 1.0 for value in p_values
    ]
    order = sorted(range(count), key=lambda index: (cleaned[index], index))
    adjusted = [1.0] * count
    running = 1.0
    for reverse_rank, index in enumerate(reversed(order), start=1):
        rank = count - reverse_rank + 1
        running = min(running, cleaned[index] * count / rank)
        adjusted[index] = min(max(running, cleaned[index]), 1.0)
    return adjusted


def evidence_score(
    *,
    robust_z: float | None,
    q_value: float | None,
    cusum_score: float,
    cusum_threshold: float,
    sequential_log_likelihood: float,
    sequential_threshold: float,
    relative_change: float | None,
    minimum_relative_effect: float,
) -> float:
    z_component = min(abs(robust_z or 0.0) / 6.0, 1.0)
    q_component = (
        0.0 if q_value is None else min(max(-math.log10(max(q_value, 1e-12)) / 6.0, 0.0), 1.0)
    )
    cusum_component = min(cusum_score / max(cusum_threshold, _EPSILON), 1.0)
    sequential_component = min(sequential_log_likelihood / max(sequential_threshold, _EPSILON), 1.0)
    magnitude_component = min(
        abs(relative_change or 0.0) / max(minimum_relative_effect * 3.0, 0.01), 1.0
    )
    return min(
        max(
            0.25 * z_component
            + 0.25 * q_component
            + 0.20 * cusum_component
            + 0.15 * sequential_component
            + 0.15 * magnitude_component,
            0.0,
        ),
        1.0,
    )


def severity_from_score(score: float, support_count: int) -> str:
    if score >= 0.85 and support_count >= 3:
        return "critical"
    if score >= 0.65 and support_count >= 2:
        return "high"
    if score >= 0.40:
        return "medium"
    return "low"

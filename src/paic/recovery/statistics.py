"""Deterministic statistical helpers for recovery verification."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import median

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class EquivalenceResult:
    equivalent: bool
    pvalue: float
    difference: float


def robust_center_scale(values: list[float], *, floor: float) -> tuple[float, float]:
    if not values:
        raise ValueError("at least one value is required")
    center = float(median(values))
    deviations = [abs(value - center) for value in values]
    scale = max(float(median(deviations)) * 1.4826, floor)
    return center, scale


def welch_tost(
    baseline: list[float],
    candidate: list[float],
    *,
    margin: float,
    alpha: float,
) -> EquivalenceResult:
    """Two one-sided Welch tests for equivalence within ``[-margin, margin]``."""

    if len(baseline) < 2 or len(candidate) < 2:
        raise ValueError("equivalence testing requires at least two values per group")
    if margin <= 0:
        raise ValueError("equivalence margin must be positive")
    left: np.ndarray = np.asarray(baseline, dtype=float)
    right: np.ndarray = np.asarray(candidate, dtype=float)
    difference = float(right.mean() - left.mean())
    left_var = float(left.var(ddof=1))
    right_var = float(right.var(ddof=1))
    variance = left_var / len(left) + right_var / len(right)
    if variance <= 0.0:
        equivalent = abs(difference) <= margin
        return EquivalenceResult(equivalent, 0.0 if equivalent else 1.0, difference)
    standard_error = sqrt(variance)
    numerator = variance * variance
    denominator = (left_var / len(left)) ** 2 / (len(left) - 1) + (right_var / len(right)) ** 2 / (
        len(right) - 1
    )
    degrees = numerator / denominator if denominator > 0.0 else min(len(left), len(right)) - 1
    lower_t = (difference + margin) / standard_error
    upper_t = (difference - margin) / standard_error
    lower_p = float(stats.t.sf(lower_t, degrees))
    upper_p = float(stats.t.cdf(upper_t, degrees))
    pvalue = max(lower_p, upper_p)
    if not isfinite(pvalue):
        pvalue = 1.0
    return EquivalenceResult(pvalue < alpha, min(max(pvalue, 0.0), 1.0), difference)


def theil_sen_slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    x: np.ndarray = np.arange(len(values), dtype=float)
    slope, _, _, _ = stats.theilslopes(np.asarray(values, dtype=float), x)
    return float(slope)

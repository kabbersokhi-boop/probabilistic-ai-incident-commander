"""Deterministic survival and causal estimators used by the impact layer."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize
from scipy.special import expit
from scipy.stats import norm

FloatArray: TypeAlias = npt.NDArray[np.float64]
BoolArray: TypeAlias = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class LogisticFit:
    coefficients: FloatArray
    probabilities: FloatArray
    converged: bool


@dataclass(frozen=True)
class CoxFit:
    coefficients: FloatArray
    standard_errors: FloatArray
    baseline_cumulative_hazard: list[tuple[float, float]]
    converged: bool


def _with_intercept(features: FloatArray) -> FloatArray:
    return np.column_stack([np.ones(features.shape[0], dtype=np.float64), features])


def standardize(features: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    means = features.mean(axis=0)
    scales = features.std(axis=0)
    scales = np.where(scales < 1e-9, 1.0, scales)
    return (features - means) / scales, means, scales


def fit_logistic(features: FloatArray, outcome: FloatArray, l2: float = 1e-4) -> LogisticFit:
    design = _with_intercept(features)

    def objective(beta: FloatArray) -> tuple[float, FloatArray]:
        linear = design @ beta
        probability = np.clip(expit(linear), 1e-9, 1.0 - 1e-9)
        loss = -float(
            np.sum(outcome * np.log(probability) + (1.0 - outcome) * np.log1p(-probability))
        )
        penalty = 0.5 * l2 * float(beta[1:] @ beta[1:])
        gradient = design.T @ (probability - outcome)
        gradient[1:] += l2 * beta[1:]
        return loss + penalty, gradient

    initial: FloatArray = np.zeros(design.shape[1], dtype=np.float64)
    result = minimize(
        lambda beta: objective(beta)[0],
        initial,
        jac=lambda beta: objective(beta)[1],
        method="BFGS",
        options={"maxiter": 500, "gtol": 1e-8},
    )
    coefficients = np.asarray(result.x, dtype=np.float64)
    probabilities = np.clip(expit(design @ coefficients), 1e-6, 1.0 - 1e-6)
    gradient_norm = float(np.linalg.norm(np.asarray(result.jac, dtype=np.float64)))
    return LogisticFit(coefficients, probabilities, bool(result.success or gradient_norm < 1e-4))


def nearest_neighbor_matches(
    scores: FloatArray, exposed: BoolArray, caliper: float
) -> dict[int, int]:
    treated = np.flatnonzero(exposed)
    controls = np.flatnonzero(~exposed)
    if treated.size == 0 or controls.size == 0:
        return {}
    logit = np.log(scores / (1.0 - scores))
    scale = float(np.std(logit)) or 1.0
    maximum_distance = caliper * scale
    unused = set(int(item) for item in controls)
    matches: dict[int, int] = {}
    for index in sorted((int(item) for item in treated), key=lambda item: (scores[item], item)):
        if not unused:
            break
        candidate = min(unused, key=lambda item: (abs(logit[index] - logit[item]), item))
        if abs(logit[index] - logit[candidate]) <= maximum_distance:
            matches[index] = candidate
            unused.remove(candidate)
    return matches


def stabilized_iptw(scores: FloatArray, exposed: BoolArray, clip: float) -> FloatArray:
    prevalence = float(exposed.mean())
    bounded = np.clip(scores, clip, 1.0 - clip)
    return np.where(exposed, prevalence / bounded, (1.0 - prevalence) / (1.0 - bounded))


def weighted_mean(values: FloatArray, weights: FloatArray) -> float:
    total = float(weights.sum())
    return float(np.sum(values * weights) / total) if total > 0 else math.nan


def weighted_ate(outcome: FloatArray, exposed: BoolArray, weights: FloatArray) -> float:
    treated = weighted_mean(outcome[exposed], weights[exposed])
    control = weighted_mean(outcome[~exposed], weights[~exposed])
    return treated - control


def matched_att(outcome: FloatArray, matches: dict[int, int]) -> float:
    if not matches:
        return math.nan
    differences = [outcome[treated] - outcome[control] for treated, control in matches.items()]
    return float(np.mean(differences))


def bootstrap_interval(
    values: FloatArray,
    statistic: Callable[[FloatArray], float],
    *,
    samples: int,
    confidence: float,
    seed: int,
) -> tuple[float, float, float]:
    if values.shape[0] == 0:
        return math.nan, math.nan, math.nan
    point = float(statistic(values))
    rng = np.random.default_rng(seed)
    estimates: FloatArray = np.empty(samples, dtype=np.float64)
    for index in range(samples):
        sampled = values[rng.integers(0, values.shape[0], size=values.shape[0])]
        estimates[index] = float(statistic(sampled))
    alpha = 1.0 - confidence
    lower, upper = np.quantile(estimates, [alpha / 2.0, 1.0 - alpha / 2.0])
    return point, float(lower), float(upper)


def kaplan_meier(
    durations: FloatArray,
    events: BoolArray,
    confidence: float,
) -> list[dict[str, float | int]]:
    if durations.size == 0:
        return []
    z = float(norm.ppf(0.5 + confidence / 2.0))
    survival = 1.0
    greenwood = 0.0
    rows: list[dict[str, float | int]] = []
    for time in np.unique(durations):
        at_risk = int(np.sum(durations >= time))
        observed = int(np.sum((durations == time) & events))
        censored = int(np.sum((durations == time) & ~events))
        if observed and at_risk:
            survival *= 1.0 - observed / at_risk
            if at_risk > observed:
                greenwood += observed / (at_risk * (at_risk - observed))
        standard_error = survival * math.sqrt(max(greenwood, 0.0))
        rows.append(
            {
                "time_days": float(time),
                "at_risk": at_risk,
                "events": observed,
                "censored": censored,
                "survival_probability": float(survival),
                "lower_ci": max(0.0, float(survival - z * standard_error)),
                "upper_ci": min(1.0, float(survival + z * standard_error)),
            }
        )
    return rows


def _cox_objective(
    beta: FloatArray, features: FloatArray, durations: FloatArray, events: BoolArray, l2: float
) -> tuple[float, FloatArray]:
    linear = np.clip(features @ beta, -30.0, 30.0)
    risk = np.exp(linear)
    log_likelihood = 0.0
    gradient = np.zeros_like(beta)
    for time in np.unique(durations[events]):
        event_mask = (durations == time) & events
        event_count = int(event_mask.sum())
        risk_mask = durations >= time
        risk_sum = float(risk[risk_mask].sum())
        if risk_sum <= 0:
            continue
        weighted_features = np.sum(features[risk_mask] * risk[risk_mask, None], axis=0) / risk_sum
        log_likelihood += float(np.sum(linear[event_mask])) - event_count * math.log(risk_sum)
        gradient += np.sum(features[event_mask], axis=0) - event_count * weighted_features
    log_likelihood -= 0.5 * l2 * float(beta @ beta)
    gradient -= l2 * beta
    return -log_likelihood, -gradient


def fit_cox(
    features: FloatArray,
    durations: FloatArray,
    events: BoolArray,
    l2: float = 1e-3,
) -> CoxFit:
    initial: FloatArray = np.zeros(features.shape[1], dtype=np.float64)
    result = minimize(
        lambda beta: _cox_objective(beta, features, durations, events, l2)[0],
        initial,
        jac=lambda beta: _cox_objective(beta, features, durations, events, l2)[1],
        method="L-BFGS-B",
        options={"maxiter": 1000, "gtol": 1e-7, "ftol": 1e-12},
    )
    beta = np.asarray(result.x, dtype=np.float64)
    raw_hessian = (
        result.hess_inv.todense() if hasattr(result.hess_inv, "todense") else result.hess_inv
    )
    inverse_hessian = np.asarray(raw_hessian, dtype=np.float64)
    standard_errors = np.sqrt(np.maximum(np.diag(inverse_hessian), 1e-12))
    linear = np.clip(features @ beta, -30.0, 30.0)
    risk = np.exp(linear)
    cumulative = 0.0
    baseline: list[tuple[float, float]] = []
    for time in np.unique(durations[events]):
        event_count = int(np.sum((durations == time) & events))
        denominator = float(risk[durations >= time].sum())
        if denominator > 0:
            cumulative += event_count / denominator
            baseline.append((float(time), cumulative))
    gradient_norm = float(np.linalg.norm(np.asarray(result.jac, dtype=np.float64)))
    return CoxFit(beta, standard_errors, baseline, bool(result.success or gradient_norm < 1e-4))


def cox_survival_probability(fit: CoxFit, features: FloatArray, horizon: float) -> FloatArray:
    cumulative = 0.0
    for time, value in fit.baseline_cumulative_hazard:
        if time <= horizon:
            cumulative = value
        else:
            break
    risk = np.exp(np.clip(features @ fit.coefficients, -30.0, 30.0))
    return np.exp(-cumulative * risk)


def concordance_index(durations: FloatArray, events: BoolArray, risk_scores: FloatArray) -> float:
    concordant = 0.0
    comparable = 0.0
    for left in range(len(durations)):
        if not events[left]:
            continue
        for right in range(len(durations)):
            if durations[left] >= durations[right]:
                continue
            comparable += 1.0
            if risk_scores[left] > risk_scores[right]:
                concordant += 1.0
            elif risk_scores[left] == risk_scores[right]:
                concordant += 0.5
    return concordant / comparable if comparable else math.nan


def brier_score(outcome: FloatArray, probability: FloatArray) -> float:
    return float(np.mean((outcome - probability) ** 2))


def standardized_mean_difference(
    features: FloatArray, exposed: BoolArray, weights: FloatArray | None = None
) -> FloatArray:
    if weights is None:
        weights = np.ones(features.shape[0], dtype=np.float64)
    result: FloatArray = np.zeros(features.shape[1], dtype=np.float64)
    for column in range(features.shape[1]):
        values = features[:, column]
        treated_mean = weighted_mean(values[exposed], weights[exposed])
        control_mean = weighted_mean(values[~exposed], weights[~exposed])
        treated_var = weighted_mean((values[exposed] - treated_mean) ** 2, weights[exposed])
        control_var = weighted_mean((values[~exposed] - control_mean) ** 2, weights[~exposed])
        pooled = math.sqrt(max((treated_var + control_var) / 2.0, 1e-12))
        result[column] = (treated_mean - control_mean) / pooled
    return result

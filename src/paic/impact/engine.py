"""Build incident-linked customer impact, survival, causal, and financial evidence."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeAlias, cast

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy.stats import norm

from paic.impact.config import ImpactConfig, IncidentDefinition
from paic.impact.schema import conform_impact_frame
from paic.impact.statistics import (
    brier_score,
    concordance_index,
    cox_survival_probability,
    fit_cox,
    fit_logistic,
    kaplan_meier,
    nearest_neighbor_matches,
    stabilized_iptw,
    standardize,
    standardized_mean_difference,
    weighted_ate,
)
from paic.impact.types import ImpactBuildResult, ImpactFrameMap
from paic.simulator.io import file_sha256, load_dataset
from paic.simulator.validation import validate_dataset_directory

FloatArray: TypeAlias = npt.NDArray[np.float64]
BoolArray: TypeAlias = npt.NDArray[np.bool_]

_FEATURE_NAMES = (
    "exposed",
    "tenure_days",
    "recency_days",
    "pre_orders",
    "pre_spend",
    "average_order_value",
    "order_frequency_30d",
    "failed_checkout_count",
    "payment_decline_count",
    "late_delivery_count",
    "return_count",
    "discount_share",
    "category_diversity",
)
_PROPENSITY_FEATURES = _FEATURE_NAMES[1:]


class ImpactBuildError(RuntimeError):
    """Raised when an impact artifact cannot be built safely."""


def _stable_uniform(customer_id: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{customer_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _window_filter(column: str, start: datetime, end: datetime) -> pl.Expr:
    return (pl.col(column) >= start) & (pl.col(column) < end)


def _exposure_counts(
    tables: dict[str, pl.DataFrame], incident: IncidentDefinition
) -> tuple[dict[str, int], int, float]:
    if incident.family == "checkout_failure":
        frame = tables["checkout_sessions"].filter(
            _window_filter("started_at", incident.started_at, incident.ended_at)
        )
        if incident.region:
            frame = frame.filter(pl.col("region") == incident.region)
        if incident.device:
            frame = frame.filter(pl.col("device") == incident.device)
        counts = frame.group_by("customer_id").len().to_dicts()
        failed = frame.filter(pl.col("completed_at").is_null())
        return (
            {str(row["customer_id"]): int(row["len"]) for row in counts},
            failed.height,
            float(failed.get_column("expected_amount").sum() or 0.0),
        )
    if incident.family == "payment_decline":
        frame = tables["payment_attempts"].filter(
            _window_filter("attempted_at", incident.started_at, incident.ended_at)
        )
        counts = frame.group_by("customer_id").len().to_dicts()
        failed = frame.filter(~pl.col("approved"))
        return (
            {str(row["customer_id"]): int(row["len"]) for row in counts},
            failed.height,
            float(failed.get_column("amount").sum() or 0.0),
        )
    if incident.family == "late_delivery":
        frame = (
            tables["shipments"]
            .filter(_window_filter("promised_delivery_at", incident.started_at, incident.ended_at))
            .join(tables["orders"].select("order_id", "customer_id", "total_amount"), on="order_id")
        )
        counts = frame.group_by("customer_id").len().to_dicts()
        failed = frame.filter(pl.col("late"))
        return (
            {str(row["customer_id"]): int(row["len"]) for row in counts},
            failed.height,
            float(failed.get_column("total_amount").sum() or 0.0),
        )
    refunds = (
        tables["refunds"]
        .filter(_window_filter("initiated_at", incident.started_at, incident.ended_at))
        .join(tables["returns"].select("return_id", "customer_id"), on="return_id")
    )
    counts = refunds.group_by("customer_id").len().to_dicts()
    failed = refunds.filter(pl.col("completed_at").is_null())
    return (
        {str(row["customer_id"]): int(row["len"]) for row in counts},
        failed.height,
        float(failed.get_column("amount").sum() or 0.0),
    )


def _customer_features(
    tables: dict[str, pl.DataFrame], config: ImpactConfig
) -> tuple[pl.DataFrame, int, float]:
    incident = config.incident
    pre_start = incident.started_at - timedelta(days=config.outcome.pre_period_days)
    horizon_end = incident.ended_at + timedelta(days=config.outcome.churn_horizon_days)
    exposure_counts, immediate_failures, immediate_loss = _exposure_counts(tables, incident)

    customers = tables["customers"].select(
        "customer_id", "created_at", "home_region", "customer_segment"
    )
    orders = tables["orders"].select(
        "order_id", "customer_id", "ordered_at", "subtotal", "discount_amount", "total_amount"
    )
    pre_orders = orders.filter(_window_filter("ordered_at", pre_start, incident.started_at))
    post_orders = orders.filter(_window_filter("ordered_at", incident.ended_at, horizon_end))
    sessions = tables["checkout_sessions"].filter(
        _window_filter("started_at", pre_start, incident.started_at)
    )
    payments = tables["payment_attempts"].filter(
        _window_filter("attempted_at", pre_start, incident.started_at)
    )
    shipments = (
        tables["shipments"]
        .filter(pl.col("promised_delivery_at") < incident.started_at)
        .join(orders.select("order_id", "customer_id"), on="order_id")
    )
    returns = tables["returns"].filter(pl.col("requested_at") < incident.started_at)
    refunds = (
        tables["refunds"]
        .filter(pl.col("initiated_at") < incident.started_at)
        .join(tables["returns"].select("return_id", "customer_id"), on="return_id")
    )
    items = (
        tables["order_items"]
        .join(pre_orders.select("order_id", "customer_id"), on="order_id")
        .join(tables["products"].select("product_id", "category"), on="product_id")
    )

    pre_by_customer = {
        str(row["customer_id"]): row
        for row in pre_orders.group_by("customer_id")
        .agg(
            pl.len().alias("pre_orders"),
            pl.col("total_amount").sum().alias("pre_spend"),
            pl.col("total_amount").mean().alias("average_order_value"),
            pl.col("discount_amount").sum().alias("discount_amount"),
            pl.col("subtotal").sum().alias("subtotal"),
            pl.col("ordered_at").max().alias("last_order_at"),
        )
        .to_dicts()
    }
    post_by_customer = {
        str(row["customer_id"]): row
        for row in post_orders.group_by("customer_id")
        .agg(pl.len().alias("post_orders"), pl.col("ordered_at").min().alias("next_order_at"))
        .to_dicts()
    }
    failed_checkout = {
        str(row["customer_id"]): int(row["len"])
        for row in sessions.filter(pl.col("completed_at").is_null())
        .group_by("customer_id")
        .len()
        .to_dicts()
    }
    declined = {
        str(row["customer_id"]): int(row["len"])
        for row in payments.filter(~pl.col("approved")).group_by("customer_id").len().to_dicts()
    }
    late = {
        str(row["customer_id"]): int(row["len"])
        for row in shipments.filter(pl.col("late")).group_by("customer_id").len().to_dicts()
    }
    return_counts = {
        str(row["customer_id"]): int(row["len"])
        for row in returns.group_by("customer_id").len().to_dicts()
    }
    refund_counts = {
        str(row["customer_id"]): int(row["len"])
        for row in refunds.group_by("customer_id").len().to_dicts()
    }
    diversity = {
        str(row["customer_id"]): int(row["category_diversity"])
        for row in items.group_by("customer_id")
        .agg(pl.col("category").n_unique().alias("category_diversity"))
        .to_dicts()
    }

    rows: list[dict[str, object]] = []
    horizon = float(config.outcome.churn_horizon_days)
    for customer in customers.iter_rows(named=True):
        customer_id = str(customer["customer_id"])
        pre = pre_by_customer.get(customer_id, {})
        post = post_by_customer.get(customer_id, {})
        pre_count = int(pre.get("pre_orders", 0))
        if pre_count < config.outcome.minimum_pre_orders:
            continue
        spend = float(pre.get("pre_spend", 0.0) or 0.0)
        average_order = float(pre.get("average_order_value", 0.0) or 0.0)
        last_order = cast(datetime | None, pre.get("last_order_at"))
        recency = (
            float((incident.started_at - last_order).total_seconds() / 86400.0)
            if last_order is not None
            else float(config.outcome.pre_period_days)
        )
        subtotal = float(pre.get("subtotal", 0.0) or 0.0)
        discount_amount = float(pre.get("discount_amount", 0.0) or 0.0)
        post_count = int(post.get("post_orders", 0))
        next_order = cast(datetime | None, post.get("next_order_at"))
        control_event_days = (
            min(float((next_order - incident.ended_at).total_seconds() / 86400.0), horizon)
            if next_order is not None
            else horizon
        )
        control_churned = next_order is None
        interactions = exposure_counts.get(customer_id, 0)
        exposed = interactions >= incident.minimum_interactions
        treated_event_days = control_event_days
        treated_churned = control_churned
        applied = False
        if exposed and config.benchmark_effect.enabled:
            applied = True
            if (
                _stable_uniform(customer_id, config.benchmark_effect.seed)
                < config.benchmark_effect.censor_probability
            ):
                treated_event_days = horizon
                treated_churned = True
            elif not control_churned:
                treated_event_days = min(
                    horizon, control_event_days + config.benchmark_effect.delay_days
                )
                treated_churned = treated_event_days >= horizon
        observed_event_days = treated_event_days if exposed else control_event_days
        churned = treated_churned if exposed else control_churned
        baseline_ltv = (
            spend / max(config.outcome.pre_period_days, 1) * config.outcome.ltv_horizon_days
        )
        rows.append(
            {
                "customer_id": customer_id,
                "index_at": incident.ended_at,
                "home_region": customer["home_region"],
                "customer_segment": customer["customer_segment"],
                "exposed": exposed,
                "exposure_interactions": interactions,
                "tenure_days": max(
                    0.0,
                    float(
                        (
                            incident.started_at - cast(datetime, customer["created_at"])
                        ).total_seconds()
                        / 86400.0
                    ),
                ),
                "recency_days": recency,
                "pre_orders": pre_count,
                "post_orders": post_count,
                "pre_spend": spend,
                "average_order_value": average_order,
                "order_frequency_30d": pre_count / config.outcome.pre_period_days * 30.0,
                "pre_purchase_rate": pre_count / config.outcome.pre_period_days,
                "post_purchase_rate": post_count / config.outcome.churn_horizon_days,
                "failed_checkout_count": failed_checkout.get(customer_id, 0),
                "payment_decline_count": declined.get(customer_id, 0),
                "late_delivery_count": late.get(customer_id, 0),
                "return_count": return_counts.get(customer_id, 0),
                "refund_count": refund_counts.get(customer_id, 0),
                "discount_share": discount_amount / subtotal if subtotal > 0 else 0.0,
                "category_diversity": diversity.get(customer_id, 0),
                "baseline_ltv": baseline_ltv,
                "control_event_days": control_event_days,
                "treated_event_days": treated_event_days,
                "observed_event_days": observed_event_days,
                "event_observed": not churned,
                "churned": churned,
                "control_churned": control_churned,
                "treated_churned": treated_churned,
                "benchmark_effect_applied": applied,
            }
        )
    frame = conform_impact_frame(
        "customer_features", pl.from_dicts(rows, infer_schema_length=None)
    ).sort("customer_id")
    return frame, immediate_failures, immediate_loss


def _matrix(frame: pl.DataFrame, names: tuple[str, ...]) -> FloatArray:
    return np.asarray(frame.select(names).to_numpy(), dtype=np.float64)


def _build_survival(features: pl.DataFrame, config: ImpactConfig) -> pl.DataFrame:
    durations = features.get_column("observed_event_days").to_numpy().astype(np.float64)
    events = features.get_column("event_observed").to_numpy().astype(np.bool_)
    exposed = features.get_column("exposed").to_numpy().astype(np.bool_)
    rows: list[dict[str, object]] = []
    for group, mask in (
        ("exposed", exposed),
        ("control", ~exposed),
        ("overall", np.ones_like(exposed)),
    ):
        for row in kaplan_meier(durations[mask], events[mask], config.causal.confidence_level):
            rows.append({"group": group, **row})
    return conform_impact_frame(
        "survival_curves", pl.from_dicts(rows, infer_schema_length=None)
    ).sort(["group", "time_days"])


def _build_cox(features: pl.DataFrame, config: ImpactConfig) -> tuple[pl.DataFrame, pl.DataFrame]:
    raw = _matrix(features, _FEATURE_NAMES)
    standardized, _, _ = standardize(raw)
    durations = features.get_column("observed_event_days").to_numpy().astype(np.float64)
    events = features.get_column("event_observed").to_numpy().astype(np.bool_)
    fit = fit_cox(standardized, durations, events)
    z_critical = float(norm.ppf(0.5 + config.causal.confidence_level / 2.0))
    rows: list[dict[str, object]] = []
    for name, coefficient, standard_error in zip(
        _FEATURE_NAMES, fit.coefficients, fit.standard_errors, strict=True
    ):
        z_value = float(coefficient / standard_error)
        rows.append(
            {
                "feature": name,
                "coefficient": float(coefficient),
                "standard_error": float(standard_error),
                "hazard_ratio": float(math.exp(coefficient)),
                "lower_ci": float(math.exp(coefficient - z_critical * standard_error)),
                "upper_ci": float(math.exp(coefficient + z_critical * standard_error)),
                "z_value": z_value,
                "p_value": float(2.0 * norm.sf(abs(z_value))),
            }
        )
    predicted_survival = cox_survival_probability(
        fit, standardized, float(config.outcome.churn_horizon_days)
    )
    churn_probability = predicted_survival
    churn = features.get_column("churned").to_numpy().astype(np.float64)
    risk_scores = standardized @ fit.coefficients
    metrics = pl.from_dicts(
        [
            {
                "model": "cox_ph",
                "metric": "c_index",
                "value": concordance_index(durations, events, risk_scores),
            },
            {
                "model": "cox_ph",
                "metric": "brier_score",
                "value": brier_score(churn, churn_probability),
            },
            {"model": "cox_ph", "metric": "converged", "value": float(fit.converged)},
        ]
    )
    return conform_impact_frame("cox_coefficients", pl.from_dicts(rows)), metrics


def _bootstrap_estimates(
    outcome: FloatArray,
    exposed: BoolArray,
    weights: FloatArray,
    matches: dict[int, int],
    config: ImpactConfig,
) -> tuple[dict[str, tuple[float, float, float, float]], FloatArray]:
    naive = float(outcome[exposed].mean() - outcome[~exposed].mean())
    iptw = weighted_ate(outcome, exposed, weights)
    pair_differences: FloatArray = np.asarray(
        [outcome[left] - outcome[right] for left, right in matches.items()], dtype=np.float64
    )
    matched = float(pair_differences.mean()) if pair_differences.size else math.nan
    rng = np.random.default_rng(config.causal.random_seed)
    naive_samples: list[float] = []
    iptw_samples: list[float] = []
    matched_samples: list[float] = []
    indices = np.arange(outcome.size)
    for _ in range(config.causal.bootstrap_samples):
        sample = rng.choice(indices, size=indices.size, replace=True)
        sample_exposed = exposed[sample]
        if sample_exposed.any() and (~sample_exposed).any():
            naive_samples.append(
                float(
                    outcome[sample][sample_exposed].mean() - outcome[sample][~sample_exposed].mean()
                )
            )
            iptw_samples.append(weighted_ate(outcome[sample], sample_exposed, weights[sample]))
        if pair_differences.size:
            matched_samples.append(
                float(rng.choice(pair_differences, size=pair_differences.size, replace=True).mean())
            )
    alpha = 1.0 - config.causal.confidence_level

    def summary(point: float, samples: list[float]) -> tuple[float, float, float, float]:
        array: FloatArray = np.asarray(samples, dtype=np.float64)
        if array.size == 0:
            return point, math.nan, math.nan, math.nan
        lower, upper = np.quantile(array, [alpha / 2.0, 1.0 - alpha / 2.0])
        return point, float(array.std(ddof=1)), float(lower), float(upper)

    return {
        "naive": summary(naive, naive_samples),
        "propensity_matched": summary(matched, matched_samples),
        "stabilized_iptw": summary(iptw, iptw_samples),
    }, np.asarray(iptw_samples, dtype=np.float64)


def _placebo_estimate(
    tables: dict[str, pl.DataFrame], features: pl.DataFrame, config: ImpactConfig
) -> float:
    shifted = config.incident.model_copy(
        update={
            "started_at": config.incident.started_at
            - timedelta(days=config.causal.placebo_shift_days),
            "ended_at": config.incident.ended_at - timedelta(days=config.causal.placebo_shift_days),
        }
    )
    counts, _, _ = _exposure_counts(tables, shifted)
    placebo: BoolArray = np.asarray(
        [
            counts.get(str(item), 0) >= shifted.minimum_interactions
            for item in features.get_column("customer_id")
        ],
        dtype=np.bool_,
    )
    outcome = features.get_column("control_churned").to_numpy().astype(np.float64)
    if not placebo.any() or not (~placebo).any():
        return math.nan
    return float(outcome[placebo].mean() - outcome[~placebo].mean())


def _build_causal(
    tables: dict[str, pl.DataFrame], features: pl.DataFrame, config: ImpactConfig
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, FloatArray]:
    exposed = features.get_column("exposed").to_numpy().astype(np.bool_)
    outcome = features.get_column("churned").to_numpy().astype(np.float64)
    raw = _matrix(features, _PROPENSITY_FEATURES)
    standardized, _, _ = standardize(raw)
    logistic = fit_logistic(standardized, exposed.astype(np.float64))
    scores = logistic.probabilities
    weights = stabilized_iptw(scores, exposed, config.causal.propensity_clip)
    matches = nearest_neighbor_matches(scores, exposed, config.causal.propensity_caliper)
    reverse_matches = {control: treated for treated, control in matches.items()}
    ids = features.get_column("customer_id").to_list()
    propensity_rows = []
    for index, customer_id in enumerate(ids):
        matched_index = matches.get(index) if exposed[index] else reverse_matches.get(index)
        propensity_rows.append(
            {
                "customer_id": customer_id,
                "exposed": bool(exposed[index]),
                "propensity_score": float(scores[index]),
                "stabilized_weight": float(weights[index]),
                "matched_customer_id": ids[matched_index] if matched_index is not None else None,
                "matched": matched_index is not None,
            }
        )
    estimates, iptw_bootstrap = _bootstrap_estimates(outcome, exposed, weights, matches, config)
    placebo = _placebo_estimate(tables, features, config)
    causal_rows: list[dict[str, object]] = []
    for estimator, (point, standard_error, lower, upper) in estimates.items():
        sample_size = len(matches) * 2 if estimator == "propensity_matched" else outcome.size
        causal_rows.append(
            {
                "estimator": estimator,
                "estimand": "incremental_churn_rate",
                "estimate": point,
                "standard_error": standard_error,
                "lower_ci": lower,
                "upper_ci": upper,
                "sample_size": sample_size,
                "treated_count": int(exposed.sum()),
                "control_count": int((~exposed).sum()),
                "placebo": False,
            }
        )
    did = float(
        (features.filter(pl.col("exposed")).get_column("post_purchase_rate").mean() or 0.0)
        - (features.filter(pl.col("exposed")).get_column("pre_purchase_rate").mean() or 0.0)
        - (features.filter(~pl.col("exposed")).get_column("post_purchase_rate").mean() or 0.0)
        + (features.filter(~pl.col("exposed")).get_column("pre_purchase_rate").mean() or 0.0)
    )
    causal_rows.append(
        {
            "estimator": "difference_in_differences",
            "estimand": "daily_purchase_rate_change",
            "estimate": did,
            "standard_error": None,
            "lower_ci": None,
            "upper_ci": None,
            "sample_size": outcome.size,
            "treated_count": int(exposed.sum()),
            "control_count": int((~exposed).sum()),
            "placebo": False,
        }
    )
    causal_rows.append(
        {
            "estimator": "naive_placebo",
            "estimand": "incremental_churn_rate",
            "estimate": placebo,
            "standard_error": None,
            "lower_ci": None,
            "upper_ci": None,
            "sample_size": outcome.size,
            "treated_count": int(exposed.sum()),
            "control_count": int((~exposed).sum()),
            "placebo": True,
        }
    )
    before = standardized_mean_difference(standardized, exposed)
    after = standardized_mean_difference(standardized, exposed, weights)
    true_att = float(
        (
            features.filter(pl.col("exposed")).get_column("treated_churned").cast(pl.Int64)
            - features.filter(pl.col("exposed")).get_column("control_churned").cast(pl.Int64)
        ).mean()
        or 0.0
    )
    metrics = pl.from_dicts(
        [
            {"model": "propensity", "metric": "converged", "value": float(logistic.converged)},
            {
                "model": "propensity",
                "metric": "max_abs_smd_before",
                "value": float(np.max(np.abs(before))),
            },
            {
                "model": "propensity",
                "metric": "max_abs_smd_after",
                "value": float(np.max(np.abs(after))),
            },
            {
                "model": "propensity",
                "metric": "matched_exposure_share",
                "value": len(matches) / max(int(exposed.sum()), 1),
            },
            {"model": "benchmark", "metric": "true_att", "value": true_att},
            {
                "model": "sensitivity",
                "metric": "bias_to_null",
                "value": abs(estimates["stabilized_iptw"][0]),
            },
            {
                "model": "placebo",
                "metric": "absolute_estimate",
                "value": abs(placebo) if math.isfinite(placebo) else math.nan,
            },
        ]
    )
    return (
        conform_impact_frame(
            "propensity_scores", pl.from_dicts(propensity_rows, infer_schema_length=None)
        ),
        conform_impact_frame(
            "causal_estimates", pl.from_dicts(causal_rows, infer_schema_length=None)
        ),
        metrics,
        iptw_bootstrap,
    )


def _build_segments(features: pl.DataFrame, propensity: pl.DataFrame) -> pl.DataFrame:
    enriched = features.join(
        propensity.select("customer_id", "stabilized_weight"),
        on="customer_id",
        how="left",
        validate="1:1",
    )
    rows: list[dict[str, object]] = []
    for segment_name in ("home_region", "customer_segment"):
        for group in enriched.partition_by(segment_name, as_dict=False, maintain_order=True):
            value = str(group.get_column(segment_name)[0])
            exposed_mask = group.get_column("exposed").to_numpy().astype(np.bool_)
            outcome = group.get_column("churned").to_numpy().astype(np.float64)
            weights = group.get_column("stabilized_weight").to_numpy().astype(np.float64)
            if exposed_mask.any() and (~exposed_mask).any():
                segment_effect = weighted_ate(outcome, exposed_mask, weights)
            else:
                segment_effect = 0.0
            exposed = group.filter(pl.col("exposed"))
            exposed_count = exposed.height
            average_ltv = float(exposed.get_column("baseline_ltv").mean() or 0.0)
            rows.append(
                {
                    "segment_name": segment_name,
                    "segment_value": value,
                    "customers": group.height,
                    "exposed_customers": exposed_count,
                    "observed_churn_rate": float(group.get_column("churned").mean() or 0.0),
                    "weighted_incremental_churn": segment_effect,
                    "revenue_at_risk": max(segment_effect, 0.0) * exposed_count * average_ltv,
                }
            )
    return conform_impact_frame(
        "segment_impact", pl.from_dicts(rows, infer_schema_length=None)
    ).sort(["segment_name", "segment_value"])


def _build_financial(
    features: pl.DataFrame,
    immediate_failures: int,
    immediate_loss: float,
    causal: pl.DataFrame,
    bootstrap: FloatArray,
    config: ImpactConfig,
) -> pl.DataFrame:
    row = causal.filter(
        (pl.col("estimator") == "stabilized_iptw") & (~pl.col("placebo"))
    ).to_dicts()[0]
    effect = float(row["estimate"])
    exposed = features.filter(pl.col("exposed"))
    exposed_count = exposed.height
    average_ltv = float(exposed.get_column("baseline_ltv").mean() or 0.0)
    incremental_customers = max(effect, 0.0) * exposed_count
    future_revenue = incremental_customers * average_ltv
    future_margin = future_revenue * config.financial.contribution_margin_rate
    operational_cost = exposed_count * (
        config.financial.support_cost_per_exposed_customer
        + config.financial.recovery_cost_per_exposed_customer
    )
    total = immediate_loss + operational_cost + future_margin
    true_att = float(
        (
            exposed.get_column("treated_churned").cast(pl.Int64)
            - exposed.get_column("control_churned").cast(pl.Int64)
        ).mean()
        or 0.0
    )
    if bootstrap.size:
        boot_total = (
            immediate_loss
            + operational_cost
            + np.maximum(bootstrap, 0.0)
            * exposed_count
            * average_ltv
            * config.financial.contribution_margin_rate
        )
        alpha = 1.0 - config.causal.confidence_level
        lower, upper = np.quantile(boot_total, [alpha / 2.0, 1.0 - alpha / 2.0])
    else:
        lower = upper = math.nan
    return conform_impact_frame(
        "financial_impact",
        pl.from_dicts(
            [
                {
                    "impact_id": config.impact_id,
                    "exposed_customers": exposed_count,
                    "immediate_failed_interactions": immediate_failures,
                    "immediate_revenue_loss": immediate_loss,
                    "support_and_recovery_cost": operational_cost,
                    "incremental_churn_rate": effect,
                    "incremental_churn_customers": incremental_customers,
                    "future_revenue_at_risk": future_revenue,
                    "future_margin_at_risk": future_margin,
                    "total_financial_impact": total,
                    "lower_ci": float(lower),
                    "upper_ci": float(upper),
                    "benchmark_true_att": true_att,
                }
            ]
        ),
    )


def _build_quality(
    features: pl.DataFrame,
    causal: pl.DataFrame,
    financial: pl.DataFrame,
    model_metrics: pl.DataFrame,
    config: ImpactConfig,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []

    def add(name: str, status: str, observed: float, expected: str, details: str) -> None:
        rows.append(
            {
                "check_name": name,
                "severity": "error"
                if status == "fail"
                else "warning"
                if status == "warn"
                else "error",
                "status": status,
                "observed_value": observed,
                "expected": expected,
                "details": details,
            }
        )

    exposed = features.filter(pl.col("exposed")).height
    controls = features.height - exposed
    add(
        "cohort.exposed_count",
        "pass" if exposed >= 20 else "warn",
        float(exposed),
        "at least 20 exposed customers preferred",
        "small exposed cohorts widen uncertainty",
    )
    add(
        "cohort.control_count",
        "pass" if controls >= 20 else "fail",
        float(controls),
        "at least 20 control customers",
        "causal comparisons require an adequate control cohort",
    )
    invalid = features.filter(
        (pl.col("observed_event_days") < 0)
        | (pl.col("observed_event_days").is_nan())
        | (pl.col("baseline_ltv") < 0)
    ).height
    add(
        "features.valid_values",
        "pass" if invalid == 0 else "fail",
        float(invalid),
        "0 invalid feature rows",
        "durations and monetary features must be valid",
    )
    main = causal.filter(
        (pl.col("estimator") == "stabilized_iptw") & (~pl.col("placebo"))
    ).to_dicts()[0]
    interval_valid = float(main["lower_ci"]) <= float(main["estimate"]) <= float(main["upper_ci"])
    add(
        "causal.interval",
        "pass" if interval_valid else "fail",
        float(interval_valid),
        "estimate inside bootstrap interval",
        "main causal estimate must reconcile to its interval",
    )
    metrics = {
        (row["model"], row["metric"]): float(row["value"]) for row in model_metrics.to_dicts()
    }
    balance = metrics.get(("propensity", "max_abs_smd_after"), math.inf)
    add(
        "causal.balance",
        "pass" if balance <= 0.25 else "warn",
        balance,
        "maximum weighted absolute SMD <= 0.25",
        "propensity weighting should improve observed covariate balance",
    )
    cox_converged = metrics.get(("cox_ph", "converged"), 0.0)
    propensity_converged = metrics.get(("propensity", "converged"), 0.0)
    add(
        "model.cox_convergence",
        "pass" if cox_converged == 1.0 else "fail",
        cox_converged,
        "Cox optimizer converged",
        "survival estimates require a stable fitted model",
    )
    add(
        "model.propensity_convergence",
        "pass" if propensity_converged == 1.0 else "fail",
        propensity_converged,
        "propensity optimizer converged",
        "causal weights require a stable exposure model",
    )
    true_att = metrics.get(("benchmark", "true_att"), math.nan)
    benchmark_covered = not config.benchmark_effect.enabled or (
        math.isfinite(true_att) and float(main["lower_ci"]) <= true_att <= float(main["upper_ci"])
    )
    add(
        "benchmark.interval_coverage",
        "pass" if benchmark_covered else "fail",
        float(benchmark_covered),
        "known synthetic ATT inside the main confidence interval",
        "the evaluated causal interval should cover the configured benchmark effect",
    )
    placebo = metrics.get(("placebo", "absolute_estimate"), math.inf)
    main_effect = abs(float(main["estimate"]))
    add(
        "causal.placebo",
        "pass" if placebo < main_effect else "warn",
        placebo,
        "placebo magnitude below main effect",
        "a large placebo effect indicates residual confounding or unstable selection",
    )
    finance = financial.to_dicts()[0]
    reconciled = abs(
        float(finance["total_financial_impact"])
        - float(finance["immediate_revenue_loss"])
        - float(finance["support_and_recovery_cost"])
        - float(finance["future_margin_at_risk"])
    )
    add(
        "financial.reconciliation",
        "pass" if reconciled <= 1e-6 else "fail",
        reconciled,
        "difference <= 1e-6",
        "financial components must reconstruct total impact",
    )
    return conform_impact_frame(
        "impact_quality_results", pl.from_dicts(rows, infer_schema_length=None)
    ).sort("check_name")


def impact_quality_error_count(frame: pl.DataFrame) -> int:
    return int(frame.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).height)


def build_impact(dataset_dir: str | Path, config: ImpactConfig) -> ImpactBuildResult:
    """Build an incident-linked customer-impact artifact from a validated source dataset."""

    source_report = validate_dataset_directory(dataset_dir)
    if not source_report.valid:
        raise ImpactBuildError("source dataset validation failed")
    manifest, tables = load_dataset(dataset_dir)
    required_end = config.incident.ended_at + timedelta(days=config.outcome.churn_horizon_days)
    if manifest.logical_start_at > config.incident.started_at - timedelta(
        days=config.outcome.pre_period_days
    ):
        raise ImpactBuildError("source dataset does not cover the configured pre-incident period")
    if manifest.logical_end_at < required_end:
        raise ImpactBuildError("source dataset does not cover the configured churn horizon")

    features, immediate_failures, immediate_loss = _customer_features(tables, config)
    exposed = features.filter(pl.col("exposed")).height
    controls = features.height - exposed
    if exposed == 0 or controls == 0:
        raise ImpactBuildError("impact analysis requires both exposed and control customers")
    survival = _build_survival(features, config)
    cox, cox_metrics = _build_cox(features, config)
    propensity, causal, causal_metrics, bootstrap = _build_causal(tables, features, config)
    model_metrics = conform_impact_frame(
        "model_metrics", pl.concat([cox_metrics, causal_metrics], how="vertical")
    ).sort(["model", "metric"])
    segments = _build_segments(features, propensity)
    financial = _build_financial(
        features, immediate_failures, immediate_loss, causal, bootstrap, config
    )
    quality = _build_quality(features, causal, financial, model_metrics, config)
    if impact_quality_error_count(quality):
        raise ImpactBuildError("impact quality checks contain errors")
    frames: ImpactFrameMap = {
        "customer_features": features,
        "survival_curves": survival,
        "cox_coefficients": cox,
        "propensity_scores": propensity,
        "causal_estimates": causal,
        "segment_impact": segments,
        "financial_impact": financial,
        "model_metrics": model_metrics,
        "impact_quality_results": quality,
    }
    return ImpactBuildResult(
        config=config,
        source_manifest=manifest,
        source_manifest_sha256=file_sha256(Path(dataset_dir) / "manifest.json"),
        tables=frames,
    )

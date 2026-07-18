"""Canonical table schemas for customer-impact artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.simulator.schema import UTC_DATETIME


@dataclass(frozen=True)
class ImpactTableSpec:
    name: str
    columns: tuple[tuple[str, pl.DataType], ...]
    primary_key: tuple[str, ...]
    timestamp_columns: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return dict(self.columns)


IMPACT_TABLE_SPECS: dict[str, ImpactTableSpec] = {
    "customer_features": ImpactTableSpec(
        name="customer_features",
        columns=(
            ("customer_id", pl.String),
            ("index_at", UTC_DATETIME),
            ("home_region", pl.String),
            ("customer_segment", pl.String),
            ("exposed", pl.Boolean),
            ("exposure_interactions", pl.Int64),
            ("tenure_days", pl.Float64),
            ("recency_days", pl.Float64),
            ("pre_orders", pl.Int64),
            ("post_orders", pl.Int64),
            ("pre_spend", pl.Float64),
            ("average_order_value", pl.Float64),
            ("order_frequency_30d", pl.Float64),
            ("pre_purchase_rate", pl.Float64),
            ("post_purchase_rate", pl.Float64),
            ("failed_checkout_count", pl.Int64),
            ("payment_decline_count", pl.Int64),
            ("late_delivery_count", pl.Int64),
            ("return_count", pl.Int64),
            ("refund_count", pl.Int64),
            ("discount_share", pl.Float64),
            ("category_diversity", pl.Int64),
            ("baseline_ltv", pl.Float64),
            ("control_event_days", pl.Float64),
            ("treated_event_days", pl.Float64),
            ("observed_event_days", pl.Float64),
            ("event_observed", pl.Boolean),
            ("churned", pl.Boolean),
            ("control_churned", pl.Boolean),
            ("treated_churned", pl.Boolean),
            ("benchmark_effect_applied", pl.Boolean),
        ),
        primary_key=("customer_id",),
        timestamp_columns=("index_at",),
    ),
    "survival_curves": ImpactTableSpec(
        name="survival_curves",
        columns=(
            ("group", pl.String),
            ("time_days", pl.Float64),
            ("at_risk", pl.Int64),
            ("events", pl.Int64),
            ("censored", pl.Int64),
            ("survival_probability", pl.Float64),
            ("lower_ci", pl.Float64),
            ("upper_ci", pl.Float64),
        ),
        primary_key=("group", "time_days"),
    ),
    "cox_coefficients": ImpactTableSpec(
        name="cox_coefficients",
        columns=(
            ("feature", pl.String),
            ("coefficient", pl.Float64),
            ("standard_error", pl.Float64),
            ("hazard_ratio", pl.Float64),
            ("lower_ci", pl.Float64),
            ("upper_ci", pl.Float64),
            ("z_value", pl.Float64),
            ("p_value", pl.Float64),
        ),
        primary_key=("feature",),
    ),
    "propensity_scores": ImpactTableSpec(
        name="propensity_scores",
        columns=(
            ("customer_id", pl.String),
            ("exposed", pl.Boolean),
            ("propensity_score", pl.Float64),
            ("stabilized_weight", pl.Float64),
            ("matched_customer_id", pl.String),
            ("matched", pl.Boolean),
        ),
        primary_key=("customer_id",),
    ),
    "causal_estimates": ImpactTableSpec(
        name="causal_estimates",
        columns=(
            ("estimator", pl.String),
            ("estimand", pl.String),
            ("estimate", pl.Float64),
            ("standard_error", pl.Float64),
            ("lower_ci", pl.Float64),
            ("upper_ci", pl.Float64),
            ("sample_size", pl.Int64),
            ("treated_count", pl.Int64),
            ("control_count", pl.Int64),
            ("placebo", pl.Boolean),
        ),
        primary_key=("estimator", "estimand", "placebo"),
    ),
    "segment_impact": ImpactTableSpec(
        name="segment_impact",
        columns=(
            ("segment_name", pl.String),
            ("segment_value", pl.String),
            ("customers", pl.Int64),
            ("exposed_customers", pl.Int64),
            ("observed_churn_rate", pl.Float64),
            ("weighted_incremental_churn", pl.Float64),
            ("revenue_at_risk", pl.Float64),
        ),
        primary_key=("segment_name", "segment_value"),
    ),
    "financial_impact": ImpactTableSpec(
        name="financial_impact",
        columns=(
            ("impact_id", pl.String),
            ("exposed_customers", pl.Int64),
            ("immediate_failed_interactions", pl.Int64),
            ("immediate_revenue_loss", pl.Float64),
            ("support_and_recovery_cost", pl.Float64),
            ("incremental_churn_rate", pl.Float64),
            ("incremental_churn_customers", pl.Float64),
            ("future_revenue_at_risk", pl.Float64),
            ("future_margin_at_risk", pl.Float64),
            ("total_financial_impact", pl.Float64),
            ("lower_ci", pl.Float64),
            ("upper_ci", pl.Float64),
            ("benchmark_true_att", pl.Float64),
        ),
        primary_key=("impact_id",),
    ),
    "model_metrics": ImpactTableSpec(
        name="model_metrics",
        columns=(
            ("model", pl.String),
            ("metric", pl.String),
            ("value", pl.Float64),
        ),
        primary_key=("model", "metric"),
    ),
    "impact_quality_results": ImpactTableSpec(
        name="impact_quality_results",
        columns=(
            ("check_name", pl.String),
            ("severity", pl.String),
            ("status", pl.String),
            ("observed_value", pl.Float64),
            ("expected", pl.String),
            ("details", pl.String),
        ),
        primary_key=("check_name",),
    ),
}

IMPACT_TABLE_ORDER: tuple[str, ...] = tuple(IMPACT_TABLE_SPECS)


def empty_impact_frame(table_name: str) -> pl.DataFrame:
    return pl.DataFrame(schema=IMPACT_TABLE_SPECS[table_name].schema)


def conform_impact_frame(table_name: str, frame: pl.DataFrame) -> pl.DataFrame:
    spec = IMPACT_TABLE_SPECS[table_name]
    missing = [name for name, _ in spec.columns if name not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} is missing columns: {', '.join(missing)}")
    return frame.select(
        [pl.col(name).cast(dtype, strict=True).alias(name) for name, dtype in spec.columns]
    )

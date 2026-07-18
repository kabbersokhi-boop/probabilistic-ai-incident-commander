from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from paic.impact.config import ImpactConfig
from paic.impact.engine import ImpactBuildError, _build_causal, _customer_features, build_impact
from paic.impact.types import ImpactBuildResult
from paic.simulator.io import load_dataset


def test_impact_build_has_expected_tables_and_reconciles(
    impact_smoke_result: ImpactBuildResult,
) -> None:
    assert set(impact_smoke_result.tables) == {
        "customer_features",
        "survival_curves",
        "cox_coefficients",
        "propensity_scores",
        "causal_estimates",
        "segment_impact",
        "financial_impact",
        "model_metrics",
        "impact_quality_results",
    }
    features = impact_smoke_result.tables["customer_features"]
    assert features.filter(pl.col("exposed")).height >= 20
    assert features.filter(~pl.col("exposed")).height >= 20
    financial = impact_smoke_result.tables["financial_impact"].to_dicts()[0]
    total = (
        financial["immediate_revenue_loss"]
        + financial["support_and_recovery_cost"]
        + financial["future_margin_at_risk"]
    )
    assert financial["total_financial_impact"] == pytest.approx(total)


def test_estimators_recover_benchmark_effect(impact_smoke_result: ImpactBuildResult) -> None:
    financial = impact_smoke_result.tables["financial_impact"].to_dicts()[0]
    estimate = float(financial["incremental_churn_rate"])
    truth = float(financial["benchmark_true_att"])
    assert estimate > 0
    assert abs(estimate - truth) < 0.15
    metrics = impact_smoke_result.tables["model_metrics"]
    assert (
        metrics.filter((pl.col("model") == "cox_ph") & (pl.col("metric") == "brier_score"))
        .get_column("value")
        .item()
        < 0.25
    )


def test_no_benchmark_effect_has_zero_true_att(
    impact_smoke_dataset_dir: Path,
    impact_smoke_config: ImpactConfig,
) -> None:
    config = impact_smoke_config.model_copy(
        update={
            "benchmark_effect": impact_smoke_config.benchmark_effect.model_copy(
                update={"enabled": False}
            )
        }
    )
    result = build_impact(impact_smoke_dataset_dir, config)
    truth = result.tables["financial_impact"].get_column("benchmark_true_att").item()
    assert truth == 0.0


def test_build_rejects_insufficient_horizon(
    smoke_dataset_dir: Path,
    impact_smoke_config: ImpactConfig,
) -> None:
    with pytest.raises(ImpactBuildError, match=r"pre-incident|churn horizon"):
        build_impact(smoke_dataset_dir, impact_smoke_config)


def test_post_incident_outcomes_do_not_change_pre_features_or_propensity_scores(
    impact_smoke_dataset_dir: Path,
    impact_smoke_config: ImpactConfig,
) -> None:
    _, tables = load_dataset(impact_smoke_dataset_dir)
    original, _, _ = _customer_features(tables, impact_smoke_config)
    original_propensity, _, _, _ = _build_causal(tables, original, impact_smoke_config)

    changed = dict(tables)
    changed["orders"] = tables["orders"].with_columns(
        pl.when(pl.col("ordered_at") >= impact_smoke_config.incident.ended_at)
        .then(pl.col("total_amount") + 10_000.0)
        .otherwise(pl.col("total_amount"))
        .alias("total_amount")
    )
    rebuilt, _, _ = _customer_features(changed, impact_smoke_config)
    rebuilt_propensity, _, _, _ = _build_causal(changed, rebuilt, impact_smoke_config)

    pre_columns = [
        "customer_id",
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
        "refund_count",
        "discount_share",
        "category_diversity",
        "baseline_ltv",
    ]
    assert original.select(pre_columns).equals(rebuilt.select(pre_columns))
    assert original_propensity.select("customer_id", "propensity_score").equals(
        rebuilt_propensity.select("customer_id", "propensity_score")
    )

"""Human- and machine-readable summaries of impact artifacts."""

from __future__ import annotations

from paic.impact.types import LoadedImpact


def build_impact_summary(loaded: LoadedImpact) -> dict[str, object]:
    financial = loaded.tables["financial_impact"].to_dicts()[0]
    causal = loaded.tables["causal_estimates"].sort("estimator").to_dicts()
    metrics = loaded.tables["model_metrics"].sort(["model", "metric"]).to_dicts()
    quality = loaded.tables["impact_quality_results"]
    return {
        "impact_id": loaded.manifest.impact_id,
        "incident_id": loaded.manifest.incident_id,
        "customers": loaded.manifest.customer_count,
        "exposed_customers": loaded.manifest.exposed_customer_count,
        "financial_impact": financial,
        "causal_estimates": causal,
        "model_metrics": metrics,
        "quality": {
            "passed": quality.filter(quality["status"] == "pass").height,
            "warnings": quality.filter(quality["status"] == "warn").height,
            "failed": quality.filter(quality["status"] == "fail").height,
        },
    }

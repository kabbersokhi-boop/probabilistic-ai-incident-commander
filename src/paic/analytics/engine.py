"""Orchestrate the deterministic commerce analytics build."""

from __future__ import annotations

from pathlib import Path

from paic.analytics.config import AnalyticsConfig
from paic.analytics.contribution import calculate_contribution_observations
from paic.analytics.funnel import calculate_funnel_observations
from paic.analytics.metrics import calculate_metric_observations
from paic.analytics.models import build_facts
from paic.analytics.quality import (
    build_data_quality_results,
    failed_quality_details,
    quality_error_count,
)
from paic.analytics.types import AnalyticsBuildResult
from paic.simulator.io import DatasetIOError, file_sha256, load_dataset
from paic.simulator.validation import validate_dataset_directory


class AnalyticsBuildError(RuntimeError):
    """Raised when source data or analytical outputs fail hard quality gates."""


def build_analytics(
    dataset_dir: str | Path,
    config: AnalyticsConfig,
) -> AnalyticsBuildResult:
    """Build metrics, funnels, contribution analysis, and quality evidence."""

    source_root = Path(dataset_dir)
    source_report = validate_dataset_directory(source_root)
    if not source_report.valid:
        source_errors = [item.message for item in source_report.issues if item.severity == "error"]
        raise AnalyticsBuildError(
            "source dataset validation failed: " + "; ".join(source_errors[:5])
        )
    try:
        source_manifest, source_tables = load_dataset(source_root)
    except DatasetIOError as exc:
        raise AnalyticsBuildError(str(exc)) from exc

    facts = build_facts(source_tables)
    metrics = calculate_metric_observations(facts, config)
    funnel = calculate_funnel_observations(facts, config)
    contributions = calculate_contribution_observations(metrics, config)
    quality = build_data_quality_results(
        source_report,
        source_tables,
        facts,
        metrics,
        funnel,
        contributions,
        config,
    )
    quality_errors = quality_error_count(quality)
    if quality_errors:
        raise AnalyticsBuildError(
            "analytical quality gates failed "
            f"({quality_errors} errors): {failed_quality_details(quality)}"
        )

    source_manifest_path = source_root / "manifest.json"
    return AnalyticsBuildResult(
        config=config,
        source_manifest=source_manifest,
        source_manifest_sha256=file_sha256(source_manifest_path),
        facts=facts,
        tables={
            "metric_observations": metrics,
            "funnel_observations": funnel,
            "contribution_observations": contributions,
            "data_quality_results": quality,
        },
    )

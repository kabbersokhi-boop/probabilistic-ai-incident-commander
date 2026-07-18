"""Validation for exported customer-impact artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from paic.impact.config import ImpactConfig
from paic.impact.engine import ImpactBuildError, build_impact
from paic.impact.io import ImpactIOError, load_impact
from paic.impact.schema import IMPACT_TABLE_ORDER, IMPACT_TABLE_SPECS
from paic.simulator.io import file_sha256


@dataclass(frozen=True)
class ImpactValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ImpactValidationReport:
    valid: bool
    issues: list[ImpactValidationIssue]
    summary: dict[str, object]


def validate_impact_directory(
    impact_dir: str | Path, *, dataset_dir: str | Path | None = None
) -> ImpactValidationReport:
    root = Path(impact_dir)
    issues: list[ImpactValidationIssue] = []
    try:
        loaded = load_impact(root)
    except ImpactIOError as exc:
        return ImpactValidationReport(False, [ImpactValidationIssue("impact.load", str(exc))], {})
    manifest = loaded.manifest
    table_map = {item.name: item for item in manifest.tables}
    if set(table_map) != set(IMPACT_TABLE_ORDER):
        issues.append(ImpactValidationIssue("manifest.tables", "impact table set is incomplete"))
    for name in IMPACT_TABLE_ORDER:
        if name not in table_map or name not in loaded.tables:
            continue
        meta = table_map[name]
        frame = loaded.tables[name]
        path = root / meta.relative_path
        spec = IMPACT_TABLE_SPECS[name]
        if file_sha256(path) != meta.sha256:
            issues.append(ImpactValidationIssue("impact.hash", f"hash mismatch for {name}"))
        if path.stat().st_size != meta.byte_size or frame.height != meta.row_count:
            issues.append(ImpactValidationIssue("impact.metadata", f"metadata mismatch for {name}"))
        if frame.schema != spec.schema:
            issues.append(ImpactValidationIssue("impact.schema", f"schema mismatch for {name}"))
        if spec.primary_key and frame.select(spec.primary_key).is_duplicated().sum() > 0:
            issues.append(ImpactValidationIssue("impact.primary_key", f"duplicate key for {name}"))
    config_path = root / "impact.config.resolved.json"
    try:
        config = ImpactConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        config = None
        issues.append(ImpactValidationIssue("impact.config", str(exc)))
    if file_sha256(config_path) != manifest.impact_config_sha256:
        issues.append(ImpactValidationIssue("impact.config_hash", "impact config hash mismatch"))
    manifest_path = root / "manifest.json"
    marker = root / "_SUCCESS"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != file_sha256(
        manifest_path
    ):
        issues.append(ImpactValidationIssue("impact.success_marker", "success marker mismatch"))
    if dataset_dir is not None:
        source_manifest = Path(dataset_dir) / "manifest.json"
        if (
            not source_manifest.is_file()
            or file_sha256(source_manifest) != manifest.source_manifest_sha256
        ):
            issues.append(
                ImpactValidationIssue("source.dataset", "source dataset does not match manifest")
            )
        elif config is not None:
            # Hashes establish file integrity. Rebuilding from the bound source establishes that
            # the tables still represent the configured incident, outcomes, and estimators.
            try:
                expected = build_impact(dataset_dir, config)
            except ImpactBuildError as exc:
                issues.append(
                    ImpactValidationIssue(
                        "impact.recompute", f"cannot recompute impact artifact: {exc}"
                    )
                )
            else:
                for name in IMPACT_TABLE_ORDER:
                    actual = loaded.tables.get(name)
                    expected_table = expected.tables[name]
                    if actual is None or not actual.equals(expected_table):
                        issues.append(
                            ImpactValidationIssue(
                                "impact.recompute",
                                f"{name} does not match a deterministic rebuild from its source dataset",
                            )
                        )
    features = loaded.tables.get("customer_features", pl.DataFrame())
    causal = loaded.tables.get("causal_estimates", pl.DataFrame())
    financial = loaded.tables.get("financial_impact", pl.DataFrame())
    quality = loaded.tables.get("impact_quality_results", pl.DataFrame())
    if not features.is_empty():
        exposed = features.filter(pl.col("exposed")).height
        if exposed != manifest.exposed_customer_count or features.height != manifest.customer_count:
            issues.append(
                ImpactValidationIssue(
                    "impact.customer_counts", "customer counts differ from manifest"
                )
            )
        invalid = features.filter(
            (pl.col("observed_event_days") < 0) | (pl.col("churned") == pl.col("event_observed"))
        ).height
        if invalid:
            issues.append(ImpactValidationIssue("impact.outcomes", f"{invalid} invalid outcomes"))
    if not causal.is_empty():
        main = causal.filter((pl.col("estimator") == "stabilized_iptw") & (~pl.col("placebo")))
        if main.height != 1:
            issues.append(
                ImpactValidationIssue("impact.main_estimate", "exactly one main estimate required")
            )
    if not financial.is_empty():
        row = financial.to_dicts()[0]
        difference = abs(
            float(row["total_financial_impact"])
            - float(row["immediate_revenue_loss"])
            - float(row["support_and_recovery_cost"])
            - float(row["future_margin_at_risk"])
        )
        if difference > 1e-6:
            issues.append(
                ImpactValidationIssue("impact.financial", "financial totals do not reconcile")
            )
    quality_errors = (
        quality.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).height
        if not quality.is_empty()
        else 0
    )
    if quality_errors != manifest.quality_error_count or quality_errors:
        issues.append(ImpactValidationIssue("impact.quality", "impact quality contains errors"))
    summary: dict[str, object] = {
        "impact_id": manifest.impact_id,
        "incident_id": manifest.incident_id,
        "customers": manifest.customer_count,
        "exposed_customers": manifest.exposed_customer_count,
        "quality_errors": quality_errors,
        "config_loaded": config is not None,
    }
    return ImpactValidationReport(not issues, issues, summary)


def impact_report_to_json(report: ImpactValidationReport) -> str:
    return json.dumps(
        {
            "valid": report.valid,
            "issues": [asdict(item) for item in report.issues],
            "summary": report.summary,
        },
        indent=2,
        sort_keys=True,
    )

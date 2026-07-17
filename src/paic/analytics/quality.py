"""Auditable quality checks for the deterministic analytical layer."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable

import polars as pl

from paic.analytics.config import AnalyticsConfig
from paic.analytics.funnel import FUNNEL_STAGES
from paic.analytics.models import expected_fact_cardinalities
from paic.analytics.registry import ANALYTIC_DIMENSIONS, METRIC_REGISTRY
from paic.analytics.schema import conform_analytics_frame
from paic.analytics.types import FactMap
from paic.simulator.types import FrameMap
from paic.simulator.validation import DatasetValidationReport

_TOLERANCE = 1e-9
_FACT_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "checkout": ("session_id",),
    "payments": ("payment_attempt_id",),
    "orders": ("order_id",),
    "order_items": ("order_item_id",),
    "inventory": ("snapshot_id",),
    "shipments": ("shipment_id",),
    "scans": ("scan_event_id",),
    "returns": ("return_id",),
    "seller_feeds": ("feed_run_id",),
    "pipelines": ("pipeline_run_id",),
    "deployments": ("deployment_id",),
}


def _quality_row(
    check_name: str,
    category: str,
    *,
    severity: str,
    status: str,
    expected: str,
    details: str,
    metric_name: str | None = None,
    time_grain: str | None = None,
    period_start: object | None = None,
    cohort_name: str | None = None,
    observed_value: float | int | None = None,
) -> dict[str, object]:
    return {
        "check_name": check_name,
        "category": category,
        "severity": severity,
        "status": status,
        "metric_name": metric_name,
        "time_grain": time_grain,
        "period_start": period_start,
        "cohort_name": cohort_name,
        "observed_value": float(observed_value) if observed_value is not None else None,
        "expected": expected,
        "details": details,
    }


def _duplicate_count(frame: pl.DataFrame, columns: Iterable[str]) -> int:
    selected = list(columns)
    if not selected or frame.is_empty():
        return 0
    return int(frame.select(selected).is_duplicated().sum())


def _source_checks(report: DatasetValidationReport) -> list[dict[str, object]]:
    errors = sum(1 for item in report.issues if item.severity == "error")
    warnings = sum(1 for item in report.issues if item.severity == "warning")
    return [
        _quality_row(
            "source.dataset_validation",
            "source",
            severity="error",
            status="pass" if errors == 0 else "fail",
            observed_value=errors,
            expected="0 source dataset validation errors",
            details=f"source validation reported {errors} errors and {warnings} warnings",
        )
    ]


def _registry_columns_for_fact(fact_name: str) -> set[str]:
    columns: set[str] = set()
    for definition in METRIC_REGISTRY.values():
        if definition.fact != fact_name:
            continue
        columns.add(definition.timestamp_column)
        columns.update(definition.supported_dimensions)
        for column in (
            definition.value_column,
            definition.numerator_column,
            definition.denominator_column,
        ):
            if column is not None:
                columns.add(column)
    return columns


def _fact_checks(source_tables: FrameMap, facts: FactMap) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    expected_rows = expected_fact_cardinalities(source_tables)
    for fact_name, expected in expected_rows.items():
        frame = facts[fact_name]
        actual = frame.height
        rows.append(
            _quality_row(
                f"fact.{fact_name}.cardinality",
                "fact_model",
                severity="error",
                status="pass" if actual == expected else "fail",
                observed_value=actual,
                expected=f"exactly {expected} rows",
                details=f"{fact_name} has {actual} rows; source cardinality is {expected}",
            )
        )
        duplicate_count = _duplicate_count(frame, _FACT_PRIMARY_KEYS[fact_name])
        rows.append(
            _quality_row(
                f"fact.{fact_name}.primary_key",
                "fact_model",
                severity="error",
                status="pass" if duplicate_count == 0 else "fail",
                observed_value=duplicate_count,
                expected="0 duplicate primary-key rows",
                details=f"checked key columns {list(_FACT_PRIMARY_KEYS[fact_name])}",
            )
        )

        required = _registry_columns_for_fact(fact_name)
        missing = sorted(required.difference(frame.columns))
        rows.append(
            _quality_row(
                f"fact.{fact_name}.metric_columns",
                "fact_model",
                severity="error",
                status="pass" if not missing else "fail",
                observed_value=len(missing),
                expected="all configured metric columns are present",
                details="missing columns: " + ", ".join(missing)
                if missing
                else "all columns present",
            )
        )

        dimension_columns = sorted(
            set(ANALYTIC_DIMENSIONS).intersection(required).intersection(frame.columns)
        )
        null_count = 0
        for column in dimension_columns:
            null_count += frame.get_column(column).null_count()
        rows.append(
            _quality_row(
                f"fact.{fact_name}.dimension_coverage",
                "fact_model",
                severity="error",
                status="pass" if null_count == 0 else "fail",
                observed_value=null_count,
                expected="0 missing analytical dimension values",
                details=f"checked dimensions: {dimension_columns}",
            )
        )
    return rows


def _metric_checks(
    metrics: pl.DataFrame, config: AnalyticsConfig, facts: FactMap
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    expected_metrics = set(config.metric_names)
    actual_metrics = (
        set(metrics.get_column("metric_name").unique().to_list())
        if not metrics.is_empty()
        else set()
    )
    missing_metrics = sorted(expected_metrics.difference(actual_metrics))
    missing_with_data = [
        name for name in missing_metrics if not facts[METRIC_REGISTRY[name].fact].is_empty()
    ]
    missing_empty_source = [
        name for name in missing_metrics if facts[METRIC_REGISTRY[name].fact].is_empty()
    ]
    if missing_with_data:
        coverage_severity = "error"
        coverage_status = "fail"
        coverage_details = "metrics missing despite non-empty source facts: " + ", ".join(
            missing_with_data
        )
        if missing_empty_source:
            coverage_details += "; empty-source metrics: " + ", ".join(missing_empty_source)
    elif missing_empty_source:
        coverage_severity = "warning"
        coverage_status = "warn"
        coverage_details = (
            "metrics without observations because their source facts are empty: "
            + ", ".join(missing_empty_source)
        )
    else:
        coverage_severity = "error"
        coverage_status = "pass"
        coverage_details = "all enabled metrics present"
    rows.append(
        _quality_row(
            "metrics.coverage",
            "metrics",
            severity=coverage_severity,
            status=coverage_status,
            observed_value=len(missing_metrics),
            expected=f"observations for all {len(expected_metrics)} enabled metrics when source facts exist",
            details=coverage_details,
        )
    )

    key_columns = [
        "metric_name",
        "time_grain",
        "period_start",
        "cohort_name",
        *ANALYTIC_DIMENSIONS,
    ]
    duplicates = _duplicate_count(metrics, key_columns)
    rows.append(
        _quality_row(
            "metrics.primary_key",
            "metrics",
            severity="error",
            status="pass" if duplicates == 0 else "fail",
            observed_value=duplicates,
            expected="0 duplicate metric observation keys",
            details="checked the canonical metric observation key",
        )
    )

    non_finite = 0
    if not metrics.is_empty():
        non_finite = metrics.filter(
            pl.col("value").is_not_null()
            & (pl.col("value").is_nan() | pl.col("value").is_infinite())
        ).height
    rows.append(
        _quality_row(
            "metrics.finite_values",
            "metrics",
            severity="error",
            status="pass" if non_finite == 0 else "fail",
            observed_value=non_finite,
            expected="0 NaN or infinite metric values",
            details="all non-null metric values must be finite",
        )
    )

    invalid_status = (
        metrics.filter(pl.col("quality_status") == "invalid").height
        if not metrics.is_empty()
        else 0
    )
    rows.append(
        _quality_row(
            "metrics.invalid_status",
            "metrics",
            severity="error",
            status="pass" if invalid_status == 0 else "fail",
            observed_value=invalid_status,
            expected="0 observations marked invalid",
            details="insufficient_data and undefined are explicit non-error states",
        )
    )

    for metric_name in sorted(actual_metrics):
        definition = METRIC_REGISTRY[metric_name]
        selected = metrics.filter(pl.col("metric_name") == metric_name)
        violations = 0
        if definition.expected_min is not None:
            violations += selected.filter(
                pl.col("value").is_not_null()
                & (pl.col("value") < definition.expected_min - _TOLERANCE)
            ).height
        if definition.expected_max is not None:
            violations += selected.filter(
                pl.col("value").is_not_null()
                & (pl.col("value") > definition.expected_max + _TOLERANCE)
            ).height
        rows.append(
            _quality_row(
                f"metrics.{metric_name}.expected_range",
                "metrics",
                severity="error",
                status="pass" if violations == 0 else "fail",
                metric_name=metric_name,
                observed_value=violations,
                expected=f"values in [{definition.expected_min}, {definition.expected_max}] when bounds exist",
                details=f"{violations} observations violated documented metric bounds",
            )
        )

        selected_defined = selected.filter(pl.col("value").is_not_null())
        arithmetic_violations = 0
        if definition.calculation in {"ratio", "ratio_of_sums", "mean"}:
            arithmetic_violations = selected_defined.filter(
                pl.col("numerator").is_null()
                | pl.col("denominator").is_null()
                | (pl.col("denominator") <= 0)
                | (
                    (pl.col("value") - (pl.col("numerator") / pl.col("denominator"))).abs()
                    > _TOLERANCE
                )
            ).height
        elif definition.calculation in {"count", "sum", "distinct_count"}:
            arithmetic_violations = selected_defined.filter(
                pl.col("numerator").is_null()
                | ((pl.col("value") - pl.col("numerator")).abs() > _TOLERANCE)
            ).height
        rows.append(
            _quality_row(
                f"metrics.{metric_name}.arithmetic",
                "metrics",
                severity="error",
                status="pass" if arithmetic_violations == 0 else "fail",
                metric_name=metric_name,
                observed_value=arithmetic_violations,
                expected="value exactly reconciles to stored numerator and denominator",
                details=f"{arithmetic_violations} observations failed arithmetic reconciliation",
            )
        )
    return rows


def _cohort_reconciliation_checks(
    metrics: pl.DataFrame, config: AnalyticsConfig
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    additive_calculations = {"count", "sum", "ratio", "ratio_of_sums", "mean"}
    overall = metrics.filter(pl.col("cohort_name") == "overall")
    for metric_name in config.metric_names:
        definition = METRIC_REGISTRY[metric_name]
        if definition.calculation not in additive_calculations:
            continue
        for grain in config.time_grains:
            overall_metric = overall.filter(
                (pl.col("metric_name") == metric_name) & (pl.col("time_grain") == grain)
            ).select(
                "period_start",
                pl.col("numerator").alias("overall_numerator"),
                pl.col("denominator").alias("overall_denominator"),
            )
            if overall_metric.is_empty():
                continue
            for cohort in config.cohorts:
                if not cohort.dimensions or not set(cohort.dimensions).issubset(
                    definition.supported_dimensions
                ):
                    continue
                grouped = (
                    metrics.filter(
                        (pl.col("metric_name") == metric_name)
                        & (pl.col("time_grain") == grain)
                        & (pl.col("cohort_name") == cohort.name)
                    )
                    .group_by("period_start")
                    .agg(
                        pl.col("numerator").sum().alias("cohort_numerator"),
                        pl.col("denominator").sum().alias("cohort_denominator"),
                    )
                )
                compared = overall_metric.join(
                    grouped, on="period_start", how="full", coalesce=True
                )
                if compared.is_empty():
                    max_difference = math.inf
                else:
                    numerator_difference = (
                        pl.col("overall_numerator").fill_null(0.0)
                        - pl.col("cohort_numerator").fill_null(0.0)
                    ).abs()
                    denominator_difference = (
                        pl.col("overall_denominator").fill_null(0.0)
                        - pl.col("cohort_denominator").fill_null(0.0)
                    ).abs()
                    value = compared.select(
                        pl.max_horizontal(numerator_difference, denominator_difference).max()
                    ).item()
                    max_difference = float(value or 0.0)
                valid = math.isfinite(max_difference) and max_difference <= _TOLERANCE
                rows.append(
                    _quality_row(
                        f"cohort.{metric_name}.{grain}.{cohort.name}.reconciliation",
                        "cohort_reconciliation",
                        severity="error",
                        status="pass" if valid else "fail",
                        metric_name=metric_name,
                        time_grain=grain,
                        cohort_name=cohort.name,
                        observed_value=max_difference if math.isfinite(max_difference) else None,
                        expected=f"maximum numerator/denominator difference <= {_TOLERANCE}",
                        details="cohort totals must reconstruct overall totals for additive metrics",
                    )
                )
    return rows


def _funnel_checks(funnel: pl.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    key_columns = [
        "time_grain",
        "period_start",
        "cohort_name",
        *ANALYTIC_DIMENSIONS,
        "stage_name",
    ]
    duplicates = _duplicate_count(funnel, key_columns)
    rows.append(
        _quality_row(
            "funnel.primary_key",
            "funnel",
            severity="error",
            status="pass" if duplicates == 0 else "fail",
            observed_value=duplicates,
            expected="0 duplicate funnel observation keys",
            details="checked the canonical funnel observation key",
        )
    )
    expected_stages = list(enumerate((name for name, _ in FUNNEL_STAGES), start=1))
    layout_violations = 0
    if not funnel.is_empty():
        group_columns = [
            "time_grain",
            "period_start",
            "cohort_name",
            *ANALYTIC_DIMENSIONS,
        ]
        layouts = funnel.group_by(group_columns, maintain_order=True).agg(
            pl.struct("stage_order", "stage_name").sort_by("stage_order").alias("stages")
        )
        layout_violations = sum(
            [
                [(int(stage["stage_order"]), str(stage["stage_name"])) for stage in row["stages"]]
                != expected_stages
                for row in layouts.iter_rows(named=True)
            ]
        )
    rows.append(
        _quality_row(
            "funnel.stage_layout",
            "funnel",
            severity="error",
            status="pass" if layout_violations == 0 else "fail",
            observed_value=layout_violations,
            expected="exactly the five canonical stages in canonical order",
            details="every funnel cohort-period must contain the complete checkout stage sequence",
        )
    )
    invalid = 0
    if not funnel.is_empty():
        group_columns = [
            "time_grain",
            "period_start",
            "cohort_name",
            *ANALYTIC_DIMENSIONS,
        ]
        starts = funnel.filter(pl.col("stage_order") == 1).select(
            *group_columns, pl.col("stage_count").alias("_start_count")
        )
        checked = funnel.join(
            starts, on=group_columns, how="left", validate="m:1", nulls_equal=True
        )
        expected_previous_conversion = pl.col("stage_count") / pl.col("previous_stage_count")
        expected_start_conversion = pl.col("stage_count") / pl.col("_start_count")
        expected_drop_rate = pl.col("drop_off_count") / pl.col("previous_stage_count")
        expected_previous_count = (
            pl.when(pl.col("stage_order") == 1)
            .then(pl.col("stage_count"))
            .otherwise(pl.col("stage_count").shift(1).over(group_columns))
        )
        invalid = checked.filter(
            (pl.col("stage_count") < 0)
            | (pl.col("previous_stage_count") < 0)
            | (pl.col("stage_count") > pl.col("previous_stage_count"))
            | (pl.col("previous_stage_count") != expected_previous_count)
            | (pl.col("drop_off_count") < 0)
            | (pl.col("drop_off_count") != pl.col("previous_stage_count") - pl.col("stage_count"))
            | (
                (pl.col("previous_stage_count") > 0)
                & (
                    pl.col("conversion_from_previous").is_null()
                    | (
                        (pl.col("conversion_from_previous") - expected_previous_conversion).abs()
                        > _TOLERANCE
                    )
                    | pl.col("drop_off_rate").is_null()
                    | ((pl.col("drop_off_rate") - expected_drop_rate).abs() > _TOLERANCE)
                )
            )
            | (
                (pl.col("previous_stage_count") == 0)
                & (
                    pl.col("conversion_from_previous").is_not_null()
                    | pl.col("drop_off_rate").is_not_null()
                )
            )
            | (
                (pl.col("_start_count") > 0)
                & (
                    pl.col("conversion_from_start").is_null()
                    | (
                        (pl.col("conversion_from_start") - expected_start_conversion).abs()
                        > _TOLERANCE
                    )
                )
            )
            | ((pl.col("_start_count") == 0) & pl.col("conversion_from_start").is_not_null())
            | (pl.col("quality_status") == "invalid")
        ).height
    rows.append(
        _quality_row(
            "funnel.monotonicity_and_arithmetic",
            "funnel",
            severity="error",
            status="pass" if invalid == 0 else "fail",
            observed_value=invalid,
            expected="0 non-monotonic or arithmetically inconsistent rows",
            details="each stage must be a subset of the preceding stage",
        )
    )
    return rows


def _contribution_checks(contributions: pl.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    key_columns = [
        "analysis_name",
        "baseline_period_start",
        "current_period_start",
        "cohort_value",
    ]
    duplicates = _duplicate_count(contributions, key_columns)
    rows.append(
        _quality_row(
            "contribution.primary_key",
            "contribution",
            severity="error",
            status="pass" if duplicates == 0 else "fail",
            observed_value=duplicates,
            expected="0 duplicate contribution keys",
            details="checked analysis, period pair, and cohort value",
        )
    )
    if contributions.is_empty():
        rows.append(
            _quality_row(
                "contribution.reconstruction",
                "contribution",
                severity="warning",
                status="warn",
                observed_value=0,
                expected="at least one adjacent-period decomposition when configured data permits",
                details="no contribution rows were generated",
            )
        )
        return rows

    grouped = contributions.group_by(
        "analysis_name", "baseline_period_start", "current_period_start"
    ).agg(
        pl.col("total_contribution").sum().alias("reconstructed_change"),
        pl.col("overall_change").first().alias("overall_change"),
        pl.col("baseline_share").sum().alias("baseline_share_sum"),
        pl.col("current_share").sum().alias("current_share_sum"),
        (
            (pl.col("rate_effect") + pl.col("mix_effect") - pl.col("total_contribution"))
            .abs()
            .max()
        ).alias("component_difference"),
    )
    max_difference = grouped.select(
        pl.max_horizontal(
            (pl.col("reconstructed_change") - pl.col("overall_change")).abs(),
            (pl.col("baseline_share_sum") - 1.0).abs(),
            (pl.col("current_share_sum") - 1.0).abs(),
            pl.col("component_difference"),
        ).max()
    ).item()
    difference = float(max_difference or 0.0)
    rows.append(
        _quality_row(
            "contribution.reconstruction",
            "contribution",
            severity="error",
            status="pass" if difference <= _TOLERANCE else "fail",
            observed_value=difference,
            expected=f"maximum decomposition difference <= {_TOLERANCE}",
            details="rate and mix effects must exactly reconstruct each overall rate change",
        )
    )
    return rows


def build_data_quality_results(
    source_report: DatasetValidationReport,
    source_tables: FrameMap,
    facts: FactMap,
    metrics: pl.DataFrame,
    funnel: pl.DataFrame,
    contributions: pl.DataFrame,
    config: AnalyticsConfig,
) -> pl.DataFrame:
    """Run deterministic source, semantic, metric, and reconciliation checks."""

    rows: list[dict[str, object]] = []
    rows.extend(_source_checks(source_report))
    rows.extend(_fact_checks(source_tables, facts))
    rows.extend(_metric_checks(metrics, config, facts))
    rows.extend(_cohort_reconciliation_checks(metrics, config))
    rows.extend(_funnel_checks(funnel))
    if config.contributions:
        rows.extend(_contribution_checks(contributions))

    frame = pl.from_dicts(rows, infer_schema_length=None) if rows else pl.DataFrame()
    return conform_analytics_frame("data_quality_results", frame).sort(
        ["category", "check_name", "metric_name", "time_grain", "cohort_name"],
        nulls_last=True,
    )


def quality_error_count(frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    return int(frame.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).height)


def quality_summary(frame: pl.DataFrame) -> dict[str, int]:
    if frame.is_empty():
        return {"passed": 0, "failed": 0, "warnings": 0, "total": 0}
    return {
        "passed": frame.filter(pl.col("status") == "pass").height,
        "failed": frame.filter(pl.col("status") == "fail").height,
        "warnings": frame.filter(pl.col("status") == "warn").height,
        "total": frame.height,
    }


def failed_quality_details(frame: pl.DataFrame) -> str:
    failures = frame.filter((pl.col("severity") == "error") & (pl.col("status") == "fail")).select(
        "check_name", "details"
    )
    return json.dumps(failures.to_dicts(), sort_keys=True)

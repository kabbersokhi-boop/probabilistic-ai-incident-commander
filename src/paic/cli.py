"""Command-line interface for contracts, synthetic data, and analytics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from paic.analytics.config import (
    AnalyticsConfig,
    AnalyticsConfigError,
    load_analytics_config,
)
from paic.analytics.engine import AnalyticsBuildError, build_analytics
from paic.analytics.io import (
    AnalyticsIOError,
    export_analytics,
    load_analytics,
)
from paic.analytics.manifest import AnalyticsManifest
from paic.analytics.summary import build_analytics_summary
from paic.analytics.validation import (
    analytics_report_to_json,
    validate_analytics_directory,
)
from paic.contracts.loader import ContractLoadError, load_contract_bundle
from paic.contracts.models import (
    EvaluationContract,
    IncidentSpec,
    ProjectContract,
    SafetyContract,
)
from paic.contracts.validator import validate_contract_bundle
from paic.simulator.config import (
    SimulationConfig,
    SimulatorConfigError,
    load_simulation_config,
)
from paic.simulator.engine import simulate
from paic.simulator.io import DatasetIOError, export_dataset, load_dataset
from paic.simulator.manifest import DatasetManifest
from paic.simulator.profile import build_profile
from paic.simulator.validation import (
    DatasetValidationReport,
    report_to_json,
    validate_dataset_directory,
    validate_simulation_result,
)


def _validate_contracts(spec_dir: Path, output_format: str) -> int:
    try:
        bundle = load_contract_bundle(spec_dir)
    except ContractLoadError as exc:
        if output_format == "json":
            print(json.dumps({"valid": False, "load_error": str(exc)}, indent=2))
        else:
            print(f"LOAD ERROR: {exc}", file=sys.stderr)
        return 2

    issues = validate_contract_bundle(bundle)
    errors = [item for item in issues if item.severity == "error"]
    if output_format == "json":
        print(
            json.dumps(
                {
                    "valid": not errors,
                    "incident_count": len(bundle.incidents),
                    "issues": [item.__dict__ for item in issues],
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif issues:
        for item in issues:
            print(f"{item.severity.upper():7} {item.code:38} {item.location}: {item.message}")
    else:
        print(
            "Project contracts are valid: "
            f"{len(bundle.incidents)} incidents, "
            f"{len(bundle.evaluation.metrics)} evaluation metrics, "
            "safety policy enforced."
        )
    return 1 if errors else 0


def _contract_summary(spec_dir: Path) -> int:
    try:
        bundle = load_contract_bundle(spec_dir)
    except ContractLoadError as exc:
        print(f"LOAD ERROR: {exc}", file=sys.stderr)
        return 2

    payload = {
        "project": bundle.project.project.model_dump(),
        "workflow": [stage.value for stage in bundle.project.workflow],
        "incident_count": len(bundle.incidents),
        "incident_families": sorted({item.family for item in bundle.incidents}),
        "incidents": [
            {
                "incident_id": item.incident_id,
                "title": item.title,
                "family": item.family,
                "difficulty": item.difficulty,
            }
            for item in bundle.incidents
        ],
        "evaluation_metric_count": len(bundle.evaluation.metrics),
        "baseline_count": len(bundle.evaluation.baselines),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _export_schemas(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    models: dict[str, type[BaseModel]] = {
        "project.schema.json": ProjectContract,
        "evaluation.schema.json": EvaluationContract,
        "safety.schema.json": SafetyContract,
        "incident.schema.json": IncidentSpec,
        "simulation-config.schema.json": SimulationConfig,
        "dataset-manifest.schema.json": DatasetManifest,
        "analytics-config.schema.json": AnalyticsConfig,
        "analytics-manifest.schema.json": AnalyticsManifest,
    }
    for filename, model in models.items():
        path = output_dir / filename
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(path)
    return 0


def _simulate(config_path: Path, output_dir: Path, overwrite: bool, output_format: str) -> int:
    try:
        config = load_simulation_config(config_path)
        result = simulate(config)
        validation = validate_simulation_result(result)
        if not validation.valid:
            if output_format == "json":
                print(report_to_json(validation))
            else:
                _print_dataset_report(validation)
            return 1
        manifest = export_dataset(result, output_dir, overwrite=overwrite)
    except (SimulatorConfigError, DatasetIOError, RuntimeError, ValueError) as exc:
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"SIMULATION ERROR: {exc}", file=sys.stderr)
        return 2

    profile = build_profile(result.tables)
    payload: dict[str, Any] = {
        "success": True,
        "simulation_id": config.simulation_id,
        "seed": config.seed,
        "output_dir": str(output_dir.resolve()),
        "manifest": manifest.model_dump(mode="json"),
        "profile": profile,
        "validation": validation.as_dict(),
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        business = cast(dict[str, Any], profile["business"])
        print(f"Generated {config.simulation_id} in {output_dir.resolve()}")
        print(f"Tables: {len(manifest.tables)}")
        print(f"Checkout sessions: {business['checkout_sessions']:,}")
        print(f"Orders: {business['orders']:,}")
        print(f"Gross order value: {business['gross_order_value']:,.2f}")
        print("Dataset validation: passed")
    return 0


def _validate_dataset(dataset_dir: Path, output_format: str) -> int:
    report = validate_dataset_directory(dataset_dir)
    if output_format == "json":
        print(report_to_json(report))
    else:
        _print_dataset_report(report)
    return 0 if report.valid else 1


def _dataset_summary(dataset_dir: Path) -> int:
    try:
        manifest, tables = load_dataset(dataset_dir)
    except DatasetIOError as exc:
        print(f"DATASET ERROR: {exc}", file=sys.stderr)
        return 2
    payload = {
        "manifest": manifest.model_dump(mode="json"),
        "profile": build_profile(tables),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _build_analytics(
    dataset_dir: Path,
    config_path: Path,
    output_dir: Path,
    overwrite: bool,
    output_format: str,
) -> int:
    try:
        config = load_analytics_config(config_path)
        result = build_analytics(dataset_dir, config)
        manifest = export_analytics(result, output_dir, overwrite=overwrite)
        loaded = load_analytics(output_dir)
        summary = build_analytics_summary(loaded)
    except (
        AnalyticsConfigError,
        AnalyticsBuildError,
        AnalyticsIOError,
        DatasetIOError,
        RuntimeError,
        ValueError,
    ) as exc:
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"ANALYTICS ERROR: {exc}", file=sys.stderr)
        return 2

    payload = {
        "success": True,
        "analytics_id": config.analytics_id,
        "output_dir": str(output_dir.resolve()),
        "manifest": manifest.model_dump(mode="json"),
        "summary": summary,
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        rows = summary["table_rows"]
        quality = summary["quality"]
        print(f"Built {config.analytics_id} in {output_dir.resolve()}")
        print(f"Metric observations: {rows['metric_observations']:,}")
        print(f"Funnel observations: {rows['funnel_observations']:,}")
        print(f"Contribution observations: {rows['contribution_observations']:,}")
        print(
            "Analytical quality: "
            f"{quality['passed']} passed, {quality['failed']} failed, "
            f"{quality['warnings']} warnings"
        )
    return 0


def _validate_analytics(
    analytics_dir: Path,
    dataset_dir: Path | None,
    output_format: str,
) -> int:
    report = validate_analytics_directory(analytics_dir, dataset_dir=dataset_dir)
    if output_format == "json":
        print(analytics_report_to_json(report))
    else:
        for issue in report.issues:
            print(
                f"{issue.severity.upper():7} {issue.code:34} {issue.table or '-'}: {issue.message}"
            )
        print("Analytics validation: " + ("passed" if report.valid else "failed"))
        if report.statistics:
            print(json.dumps(report.statistics, indent=2, sort_keys=True))
    return 0 if report.valid else 1


def _analytics_summary(analytics_dir: Path) -> int:
    try:
        loaded = load_analytics(analytics_dir)
    except AnalyticsIOError as exc:
        print(f"ANALYTICS ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(build_analytics_summary(loaded), indent=2, sort_keys=True, default=str))
    return 0


def _print_dataset_report(report: DatasetValidationReport) -> None:
    for issue in report.issues:
        print(f"{issue.severity.upper():7} {issue.code:36} {issue.table or '-'}: {issue.message}")
    print("Dataset validation: " + ("passed" if report.valid else "failed"))
    if report.statistics:
        print(json.dumps(report.statistics, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paic",
        description="Probabilistic AI Incident Commander development toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate project contracts.")
    validate_parser.add_argument("--spec-dir", type=Path, default=Path("specs"))
    validate_parser.add_argument("--format", choices=("text", "json"), default="text")

    summary_parser = subparsers.add_parser("summary", help="Print contract summary.")
    summary_parser.add_argument("--spec-dir", type=Path, default=Path("specs"))

    schema_parser = subparsers.add_parser(
        "export-schemas", help="Export JSON Schemas for external tooling."
    )
    schema_parser.add_argument("--output-dir", type=Path, default=Path("schemas"))

    simulate_parser = subparsers.add_parser(
        "simulate", help="Generate an incident-free synthetic commerce dataset."
    )
    simulate_parser.add_argument("--config", type=Path, required=True)
    simulate_parser.add_argument("--output-dir", type=Path, required=True)
    simulate_parser.add_argument("--overwrite", action="store_true")
    simulate_parser.add_argument("--format", choices=("text", "json"), default="text")

    dataset_parser = subparsers.add_parser("dataset", help="Inspect exported datasets.")
    dataset_subparsers = dataset_parser.add_subparsers(dest="dataset_command", required=True)
    dataset_validate = dataset_subparsers.add_parser(
        "validate", help="Validate an exported dataset."
    )
    dataset_validate.add_argument("--dataset-dir", type=Path, required=True)
    dataset_validate.add_argument("--format", choices=("text", "json"), default="text")
    dataset_summary = dataset_subparsers.add_parser(
        "summary", help="Print a dataset manifest and business profile."
    )
    dataset_summary.add_argument("--dataset-dir", type=Path, required=True)

    analytics_parser = subparsers.add_parser(
        "analytics", help="Build and inspect deterministic analytical artifacts."
    )
    analytics_subparsers = analytics_parser.add_subparsers(dest="analytics_command", required=True)
    analytics_build = analytics_subparsers.add_parser(
        "build", help="Build metrics, funnels, contributions, and quality evidence."
    )
    analytics_build.add_argument("--dataset-dir", type=Path, required=True)
    analytics_build.add_argument("--config", type=Path, required=True)
    analytics_build.add_argument("--output-dir", type=Path, required=True)
    analytics_build.add_argument("--overwrite", action="store_true")
    analytics_build.add_argument("--format", choices=("text", "json"), default="text")
    analytics_validate = analytics_subparsers.add_parser(
        "validate", help="Validate an exported analytical artifact."
    )
    analytics_validate.add_argument("--analytics-dir", type=Path, required=True)
    analytics_validate.add_argument("--dataset-dir", type=Path)
    analytics_validate.add_argument("--format", choices=("text", "json"), default="text")
    analytics_summary = analytics_subparsers.add_parser(
        "summary", help="Print analytical manifest, quality, latest metrics, and funnel."
    )
    analytics_summary.add_argument("--analytics-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return _validate_contracts(args.spec_dir, args.format)
    if args.command == "summary":
        return _contract_summary(args.spec_dir)
    if args.command == "export-schemas":
        return _export_schemas(args.output_dir)
    if args.command == "simulate":
        return _simulate(args.config, args.output_dir, args.overwrite, args.format)
    if args.command == "dataset" and args.dataset_command == "validate":
        return _validate_dataset(args.dataset_dir, args.format)
    if args.command == "dataset" and args.dataset_command == "summary":
        return _dataset_summary(args.dataset_dir)
    if args.command == "analytics" and args.analytics_command == "build":
        return _build_analytics(
            args.dataset_dir,
            args.config,
            args.output_dir,
            args.overwrite,
            args.format,
        )
    if args.command == "analytics" and args.analytics_command == "validate":
        return _validate_analytics(args.analytics_dir, args.dataset_dir, args.format)
    if args.command == "analytics" and args.analytics_command == "summary":
        return _analytics_summary(args.analytics_dir)
    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

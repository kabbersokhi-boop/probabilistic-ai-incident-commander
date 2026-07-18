"""Command-line interface for contracts, synthetic data, analytics, and detection."""

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
from paic.detection.config import (
    DetectionConfig,
    DetectionConfigError,
    load_detection_config,
)
from paic.detection.engine import DetectionBuildError, build_detection
from paic.detection.io import DetectionIOError, export_detection, load_detection
from paic.detection.manifest import DetectionManifest
from paic.detection.summary import build_detection_summary
from paic.detection.validation import (
    detection_report_to_json,
    validate_detection_directory,
)
from paic.evidence.config import EvidenceConfig, EvidenceConfigError, load_evidence_config
from paic.evidence.engine import EvidenceBuildError, build_evidence
from paic.evidence.io import EvidenceIOError, export_evidence, load_evidence
from paic.evidence.manifest import EvidenceManifest
from paic.evidence.summary import build_evidence_summary
from paic.evidence.validation import evidence_report_to_json, validate_evidence_directory
from paic.impact.config import ImpactConfig, ImpactConfigError, load_impact_config
from paic.impact.engine import ImpactBuildError, build_impact
from paic.impact.io import ImpactIOError, export_impact, load_impact
from paic.impact.manifest import ImpactManifest
from paic.impact.summary import build_impact_summary
from paic.impact.validation import impact_report_to_json, validate_impact_directory
from paic.investigation.artifact import (
    InvestigationArtifactError,
    export_investigation,
    replay_investigation,
    validate_investigation,
)
from paic.investigation.config import (
    InvestigationConfig,
    InvestigationConfigError,
    load_investigation_config,
)
from paic.investigation.evaluation import EvaluationCase, evaluate_cases
from paic.investigation.manifest import InvestigationManifest
from paic.investigation.models import InvestigationReport, InvestigationRequest, ProviderResponse
from paic.investigation.orchestrator import InvestigationError, Investigator, scripted_factory
from paic.investigation.provider import ScriptedProvider
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
from paic.tools.gateway import Gateway, GatewayError
from paic.tools.ledger import AuditLedger
from paic.tools.models import ToolError, ToolRequest, ToolResponse


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
        "detection-config.schema.json": DetectionConfig,
        "detection-manifest.schema.json": DetectionManifest,
        "impact-config.schema.json": ImpactConfig,
        "impact-manifest.schema.json": ImpactManifest,
        "evidence-config.schema.json": EvidenceConfig,
        "evidence-manifest.schema.json": EvidenceManifest,
        "tool-request.schema.json": ToolRequest,
        "tool-response.schema.json": ToolResponse,
        "tool-error.schema.json": ToolError,
        "investigation-config.schema.json": InvestigationConfig,
        "investigation-request.schema.json": InvestigationRequest,
        "investigation-report.schema.json": InvestigationReport,
        "investigation-manifest.schema.json": InvestigationManifest,
        "investigation-evaluation-case.schema.json": EvaluationCase,
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


def _build_detection(
    analytics_dir: Path,
    config_path: Path,
    output_dir: Path,
    overwrite: bool,
    output_format: str,
) -> int:
    try:
        config = load_detection_config(config_path)
        result = build_detection(analytics_dir, config)
        manifest = export_detection(result, output_dir, overwrite=overwrite)
        loaded = load_detection(output_dir)
        summary = build_detection_summary(loaded)
    except (
        DetectionConfigError,
        DetectionBuildError,
        DetectionIOError,
        AnalyticsIOError,
        RuntimeError,
        ValueError,
    ) as exc:
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"DETECTION ERROR: {exc}", file=sys.stderr)
        return 2

    payload = {
        "success": True,
        "detection_id": config.detection_id,
        "output_dir": str(output_dir.resolve()),
        "manifest": manifest.model_dump(mode="json"),
        "summary": summary,
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"Built {config.detection_id} in {output_dir.resolve()}")
        print(f"Scored observations: {manifest.observation_count:,}")
        print(f"Anomaly observations: {manifest.anomaly_observation_count:,}")
        print(f"Anomaly events: {manifest.anomaly_event_count:,}")
        if manifest.benchmark_scenario_count:
            print(
                "Benchmark: "
                f"precision={manifest.benchmark_precision:.3f}, "
                f"scenario_recall={manifest.benchmark_scenario_recall:.3f}, "
                f"false_positive_rate={manifest.benchmark_false_positive_rate:.4f}"
            )
        print("Detection quality: passed")
    return 0


def _validate_detection(detection_dir: Path, analytics_dir: Path | None, output_format: str) -> int:
    report = validate_detection_directory(detection_dir, analytics_dir=analytics_dir)
    if output_format == "json":
        print(detection_report_to_json(report))
    else:
        for issue in report.issues:
            print(f"{issue.severity.upper():7} {issue.code:34} {issue.message}")
        print("Detection validation: " + ("passed" if report.valid else "failed"))
        if report.summary:
            print(json.dumps(report.summary, indent=2, sort_keys=True))
    return 0 if report.valid else 1


def _detection_summary(detection_dir: Path) -> int:
    try:
        loaded = load_detection(detection_dir)
    except DetectionIOError as exc:
        print(f"DETECTION ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(build_detection_summary(loaded), indent=2, sort_keys=True, default=str))
    return 0


def _build_impact(
    dataset_dir: Path,
    config_path: Path,
    output_dir: Path,
    overwrite: bool,
    output_format: str,
) -> int:
    try:
        config = load_impact_config(config_path)
        result = build_impact(dataset_dir, config)
        manifest = export_impact(result, output_dir, overwrite=overwrite)
        loaded = load_impact(output_dir)
        summary = build_impact_summary(loaded)
    except (
        ImpactConfigError,
        ImpactBuildError,
        ImpactIOError,
        DatasetIOError,
        RuntimeError,
        ValueError,
    ) as exc:
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"IMPACT ERROR: {exc}", file=sys.stderr)
        return 2
    payload = {
        "success": True,
        "impact_id": config.impact_id,
        "output_dir": str(output_dir.resolve()),
        "manifest": manifest.model_dump(mode="json"),
        "summary": summary,
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        financial = cast(dict[str, Any], summary["financial_impact"])
        print(f"Built {config.impact_id} in {output_dir.resolve()}")
        print(f"Customers: {manifest.customer_count:,}")
        print(f"Exposed customers: {manifest.exposed_customer_count:,}")
        print(f"Incremental churn rate: {financial['incremental_churn_rate']:.4f}")
        print(f"Total financial impact: {financial['total_financial_impact']:,.2f}")
        print("Impact quality: passed")
    return 0


def _validate_impact(impact_dir: Path, dataset_dir: Path | None, output_format: str) -> int:
    report = validate_impact_directory(impact_dir, dataset_dir=dataset_dir)
    if output_format == "json":
        print(impact_report_to_json(report))
    else:
        for issue in report.issues:
            print(f"{issue.severity.upper():7} {issue.code:34} {issue.message}")
        print("Impact validation: " + ("passed" if report.valid else "failed"))
        if report.summary:
            print(json.dumps(report.summary, indent=2, sort_keys=True))
    return 0 if report.valid else 1


def _impact_summary(impact_dir: Path) -> int:
    try:
        loaded = load_impact(impact_dir)
    except ImpactIOError as exc:
        print(f"IMPACT ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(build_impact_summary(loaded), indent=2, sort_keys=True, default=str))
    return 0


def _build_evidence(
    dataset_dir: Path,
    config_path: Path,
    output_dir: Path,
    overwrite: bool,
    output_format: str,
    analytics_dir: Path | None,
    detection_dir: Path | None,
    impact_dir: Path | None,
) -> int:
    try:
        if detection_dir is not None and analytics_dir is None:
            raise EvidenceBuildError("--detection-dir requires --analytics-dir")
        config = load_evidence_config(config_path)
        result = build_evidence(
            dataset_dir,
            config,
            analytics_dir=analytics_dir,
            detection_dir=detection_dir,
            impact_dir=impact_dir,
        )
        manifest = export_evidence(result, output_dir, overwrite=overwrite)
        loaded = load_evidence(output_dir)
        summary = build_evidence_summary(loaded)
    except (
        EvidenceConfigError,
        EvidenceBuildError,
        EvidenceIOError,
        DatasetIOError,
        RuntimeError,
        ValueError,
    ) as exc:
        if output_format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"EVIDENCE ERROR: {exc}", file=sys.stderr)
        return 2
    payload = {
        "success": True,
        "evidence_id": config.evidence_id,
        "output_dir": str(output_dir.resolve()),
        "manifest": manifest.model_dump(mode="json"),
        "summary": summary,
    }
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"Built {config.evidence_id} in {output_dir.resolve()}")
        print(f"Evidence records: {manifest.evidence_record_count:,}")
        print(f"Timeline events: {manifest.timeline_event_count:,}")
        print(f"Lineage: {manifest.lineage_node_count} nodes, {manifest.lineage_edge_count} edges")
        print("Evidence quality: passed")
    return 0


def _validate_evidence(
    evidence_dir: Path,
    dataset_dir: Path | None,
    analytics_dir: Path | None,
    detection_dir: Path | None,
    impact_dir: Path | None,
    output_format: str,
) -> int:
    report = validate_evidence_directory(
        evidence_dir,
        dataset_dir=dataset_dir,
        analytics_dir=analytics_dir,
        detection_dir=detection_dir,
        impact_dir=impact_dir,
    )
    if output_format == "json":
        print(evidence_report_to_json(report))
    else:
        for issue in report.issues:
            print(f"{issue.severity.upper():7} {issue.code:34} {issue.message}")
        print("Evidence validation: " + ("passed" if report.valid else "failed"))
        if report.summary:
            print(json.dumps(report.summary, indent=2, sort_keys=True))
    return 0 if report.valid else 1


def _evidence_summary(evidence_dir: Path) -> int:
    try:
        loaded = load_evidence(evidence_dir)
    except EvidenceIOError as exc:
        print(f"EVIDENCE ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(build_evidence_summary(loaded), indent=2, sort_keys=True, default=str))
    return 0


def _tools_list() -> int:
    print(json.dumps(Gateway.list_tools(), indent=2, sort_keys=True))
    return 0


def _tools_invoke(request_path: Path) -> int:
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
        request = ToolRequest.model_validate(raw)
        response = Gateway().invoke(request)
    except (OSError, ValueError, GatewayError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(response.model_dump_json(indent=2))
    return 0 if response.execution_status == "success" else 1


def _tools_audit_validate(audit_dir: Path) -> int:
    try:
        AuditLedger(audit_dir).validate()
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"valid": True, "audit_dir": str(audit_dir)}, indent=2))
    return 0


def _scripted_providers(path: Path, config: InvestigationConfig) -> dict[str, ScriptedProvider]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("provider script must be an object keyed by model")
    providers: dict[str, ScriptedProvider] = {}
    for route in config.provider.models:
        values = raw.get(route.model)
        if not isinstance(values, list):
            raise ValueError(f"provider script is missing model {route.model}")
        providers[route.model] = ScriptedProvider(
            route.model, [ProviderResponse.model_validate(item) for item in values]
        )
    return providers


def _investigate_models(config_path: Path) -> int:
    try:
        config = load_investigation_config(config_path)
    except InvestigationConfigError as exc:
        print(f"INVESTIGATION ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            [item.model_dump(mode="json") for item in config.provider.models],
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _investigate_run(
    request_path: Path,
    config_path: Path,
    output_dir: Path,
    overwrite: bool,
    provider_script: Path | None,
) -> int:
    try:
        config = load_investigation_config(config_path)
        request = InvestigationRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
        factory = None
        if provider_script is not None:
            factory = scripted_factory(_scripted_providers(provider_script, config))
        report, transcript = Investigator(config, provider_factory=factory).run(request)
        manifest = export_investigation(
            report, config, request, transcript, output_dir, overwrite=overwrite
        )
    except (
        OSError,
        ValueError,
        InvestigationConfigError,
        InvestigationError,
        InvestigationArtifactError,
    ) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "success": True,
                "status": report.status,
                "selected_hypothesis_id": report.selected_hypothesis_id,
                "confidence": report.confidence,
                "manifest": manifest.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _investigate_validate(
    investigation_dir: Path,
    dataset_dir: Path | None,
    analytics_dir: Path | None,
    detection_dir: Path | None,
    impact_dir: Path | None,
    evidence_dir: Path | None,
) -> int:
    issues = validate_investigation(
        investigation_dir,
        dataset_dir=dataset_dir,
        analytics_dir=analytics_dir,
        detection_dir=detection_dir,
        impact_dir=impact_dir,
        evidence_dir=evidence_dir,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _investigate_benchmark(cases_path: Path) -> int:
    try:
        raw = json.loads(cases_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("benchmark cases must be a list")
        summary = evaluate_cases([EvaluationCase.model_validate(item) for item in raw])
    except (OSError, ValueError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(summary.model_dump_json(indent=2))
    return 0


def _investigate_replay(investigation_dir: Path) -> int:
    try:
        report = replay_investigation(investigation_dir)
    except InvestigationArtifactError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
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

    detection_parser = subparsers.add_parser(
        "detection", help="Build and inspect deterministic anomaly-detection artifacts."
    )
    detection_subparsers = detection_parser.add_subparsers(dest="detection_command", required=True)
    detection_build = detection_subparsers.add_parser(
        "build", help="Build robust baselines, detector scores, events, and benchmarks."
    )
    detection_build.add_argument("--analytics-dir", type=Path, required=True)
    detection_build.add_argument("--config", type=Path, required=True)
    detection_build.add_argument("--output-dir", type=Path, required=True)
    detection_build.add_argument("--overwrite", action="store_true")
    detection_build.add_argument("--format", choices=("text", "json"), default="text")
    detection_validate = detection_subparsers.add_parser(
        "validate", help="Validate an exported anomaly-detection artifact."
    )
    detection_validate.add_argument("--detection-dir", type=Path, required=True)
    detection_validate.add_argument("--analytics-dir", type=Path)
    detection_validate.add_argument("--format", choices=("text", "json"), default="text")
    detection_summary = detection_subparsers.add_parser(
        "summary", help="Print detection manifest, benchmark, quality, and latest events."
    )
    detection_summary.add_argument("--detection-dir", type=Path, required=True)

    impact_parser = subparsers.add_parser(
        "impact", help="Build and inspect customer-impact and survival artifacts."
    )
    impact_subparsers = impact_parser.add_subparsers(dest="impact_command", required=True)
    impact_build = impact_subparsers.add_parser(
        "build", help="Build customer features, survival, causal, and financial impact evidence."
    )
    impact_build.add_argument("--dataset-dir", type=Path, required=True)
    impact_build.add_argument("--config", type=Path, required=True)
    impact_build.add_argument("--output-dir", type=Path, required=True)
    impact_build.add_argument("--overwrite", action="store_true")
    impact_build.add_argument("--format", choices=("text", "json"), default="text")
    impact_validate = impact_subparsers.add_parser(
        "validate", help="Validate an exported customer-impact artifact."
    )
    impact_validate.add_argument("--impact-dir", type=Path, required=True)
    impact_validate.add_argument("--dataset-dir", type=Path)
    impact_validate.add_argument("--format", choices=("text", "json"), default="text")
    impact_summary = impact_subparsers.add_parser(
        "summary", help="Print impact, causal, survival-model, and financial summaries."
    )
    impact_summary.add_argument("--impact-dir", type=Path, required=True)

    evidence_parser = subparsers.add_parser(
        "evidence", help="Build and inspect operational evidence and lineage artifacts."
    )
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command", required=True)
    evidence_build = evidence_subparsers.add_parser(
        "build", help="Build structured operational evidence, lineage, and an incident timeline."
    )
    evidence_build.add_argument("--dataset-dir", type=Path, required=True)
    evidence_build.add_argument("--analytics-dir", type=Path)
    evidence_build.add_argument("--detection-dir", type=Path)
    evidence_build.add_argument("--impact-dir", type=Path)
    evidence_build.add_argument("--config", type=Path, required=True)
    evidence_build.add_argument("--output-dir", type=Path, required=True)
    evidence_build.add_argument("--overwrite", action="store_true")
    evidence_build.add_argument("--format", choices=("text", "json"), default="text")
    evidence_validate = evidence_subparsers.add_parser(
        "validate", help="Validate an exported operational evidence artifact."
    )
    evidence_validate.add_argument("--evidence-dir", type=Path, required=True)
    evidence_validate.add_argument("--dataset-dir", type=Path)
    evidence_validate.add_argument("--analytics-dir", type=Path)
    evidence_validate.add_argument("--detection-dir", type=Path)
    evidence_validate.add_argument("--impact-dir", type=Path)
    evidence_validate.add_argument("--format", choices=("text", "json"), default="text")
    evidence_summary = evidence_subparsers.add_parser(
        "summary", help="Print evidence, lineage, health, timeline, and quality summaries."
    )
    evidence_summary.add_argument("--evidence-dir", type=Path, required=True)

    tools_parser = subparsers.add_parser(
        "tools", help="Invoke the read-only Governed Tool Gateway."
    )
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_subparsers.add_parser("list", help="List available governed tools.")
    tools_invoke = tools_subparsers.add_parser("invoke", help="Invoke a governed tool request.")
    tools_invoke.add_argument("--request", type=Path, required=True)
    tools_audit = tools_subparsers.add_parser("audit", help="Validate the invocation ledger.")
    tools_audit_subparsers = tools_audit.add_subparsers(dest="tools_audit_command", required=True)
    tools_audit_validate = tools_audit_subparsers.add_parser("validate")
    tools_audit_validate.add_argument("--audit-dir", type=Path, required=True)

    investigate_parser = subparsers.add_parser(
        "investigate", help="Run evidence-grounded probabilistic agentic investigation."
    )
    investigate_subparsers = investigate_parser.add_subparsers(
        dest="investigate_command", required=True
    )
    investigate_models = investigate_subparsers.add_parser(
        "models", help="List configured model routes."
    )
    investigate_models.add_argument("--config", type=Path, required=True)
    investigate_run = investigate_subparsers.add_parser("run", help="Run a bounded investigation.")
    investigate_run.add_argument("--request", type=Path, required=True)
    investigate_run.add_argument("--config", type=Path, required=True)
    investigate_run.add_argument("--output-dir", type=Path, required=True)
    investigate_run.add_argument("--overwrite", action="store_true")
    investigate_run.add_argument(
        "--provider-script",
        type=Path,
        help="Offline deterministic provider responses for CI/testing.",
    )
    investigate_validate = investigate_subparsers.add_parser(
        "validate", help="Validate an exported investigation artifact."
    )
    investigate_validate.add_argument("--investigation-dir", type=Path, required=True)
    investigate_validate.add_argument("--dataset-dir", type=Path)
    investigate_validate.add_argument("--analytics-dir", type=Path)
    investigate_validate.add_argument("--detection-dir", type=Path)
    investigate_validate.add_argument("--impact-dir", type=Path)
    investigate_validate.add_argument("--evidence-dir", type=Path)
    investigate_benchmark = investigate_subparsers.add_parser(
        "benchmark", help="Evaluate exported investigation reports against hidden truth."
    )
    investigate_benchmark.add_argument("--cases", type=Path, required=True)
    investigate_replay = investigate_subparsers.add_parser(
        "replay", help="Recompute and print a report without calling a model."
    )
    investigate_replay.add_argument("--investigation-dir", type=Path, required=True)
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
    if args.command == "detection" and args.detection_command == "build":
        return _build_detection(
            args.analytics_dir,
            args.config,
            args.output_dir,
            args.overwrite,
            args.format,
        )
    if args.command == "detection" and args.detection_command == "validate":
        return _validate_detection(args.detection_dir, args.analytics_dir, args.format)
    if args.command == "detection" and args.detection_command == "summary":
        return _detection_summary(args.detection_dir)
    if args.command == "impact" and args.impact_command == "build":
        return _build_impact(
            args.dataset_dir, args.config, args.output_dir, args.overwrite, args.format
        )
    if args.command == "impact" and args.impact_command == "validate":
        return _validate_impact(args.impact_dir, args.dataset_dir, args.format)
    if args.command == "impact" and args.impact_command == "summary":
        return _impact_summary(args.impact_dir)
    if args.command == "evidence" and args.evidence_command == "build":
        return _build_evidence(
            args.dataset_dir,
            args.config,
            args.output_dir,
            args.overwrite,
            args.format,
            args.analytics_dir,
            args.detection_dir,
            args.impact_dir,
        )
    if args.command == "evidence" and args.evidence_command == "validate":
        return _validate_evidence(
            args.evidence_dir,
            args.dataset_dir,
            args.analytics_dir,
            args.detection_dir,
            args.impact_dir,
            args.format,
        )
    if args.command == "evidence" and args.evidence_command == "summary":
        return _evidence_summary(args.evidence_dir)
    if args.command == "tools" and args.tools_command == "list":
        return _tools_list()
    if args.command == "tools" and args.tools_command == "invoke":
        return _tools_invoke(args.request)
    if (
        args.command == "tools"
        and args.tools_command == "audit"
        and args.tools_audit_command == "validate"
    ):
        return _tools_audit_validate(args.audit_dir)
    if args.command == "investigate" and args.investigate_command == "models":
        return _investigate_models(args.config)
    if args.command == "investigate" and args.investigate_command == "run":
        return _investigate_run(
            args.request, args.config, args.output_dir, args.overwrite, args.provider_script
        )
    if args.command == "investigate" and args.investigate_command == "validate":
        return _investigate_validate(
            args.investigation_dir,
            args.dataset_dir,
            args.analytics_dir,
            args.detection_dir,
            args.impact_dir,
            args.evidence_dir,
        )
    if args.command == "investigate" and args.investigate_command == "benchmark":
        return _investigate_benchmark(args.cases)
    if args.command == "investigate" and args.investigate_command == "replay":
        return _investigate_replay(args.investigation_dir)
    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

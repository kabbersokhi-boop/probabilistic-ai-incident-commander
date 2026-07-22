"""Read-only inspection of a complete PAIC artifact workspace."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from paic.analytics.validation import validate_analytics_directory
from paic.artifacts.lease import artifact_reader_leases
from paic.detection.validation import validate_detection_directory
from paic.evaluation.artifact import replay_evaluation
from paic.evidence.io import load_evidence
from paic.evidence.validation import validate_evidence_directory
from paic.impact.validation import validate_impact_directory
from paic.investigation.artifact import (
    load_investigation,
    replay_investigation,
    validate_investigation,
)
from paic.recovery.artifact import load_recovery, validate_recovery
from paic.remediation.artifact import (
    load_execution,
    load_plan,
    validate_execution,
    validate_plan,
)
from paic.simulator.validation import validate_dataset_directory
from paic.tui.models import (
    StageSnapshot,
    StageStatus,
    WorkspaceConfig,
    WorkspaceSnapshot,
)

_STAGE_TITLES = {
    "dataset": "Synthetic data",
    "analytics": "Business metrics",
    "detection": "Incident detection",
    "impact": "Customer impact",
    "evidence": "Operational evidence",
    "investigation": "Root-cause investigation",
    "remediation": "Controlled remediation",
    "recovery": "Recovery verification",
    "evaluation": "Safety evaluation",
}


def _missing(key: str, path: Path | None) -> StageSnapshot:
    if path is None:
        return StageSnapshot(
            key=key,
            title=_STAGE_TITLES[key],
            status="not_configured",
            summary="This stage is not configured in the workspace.",
        )
    return StageSnapshot(
        key=key,
        title=_STAGE_TITLES[key],
        status="missing",
        summary="The configured artifact does not exist.",
        path=str(path),
        issues=[f"Missing path: {path}"],
    )


def _path_ready(key: str, path: Path | None, *, file_ok: bool = False) -> StageSnapshot | None:
    if path is None or not path.exists():
        return _missing(key, path)
    valid_type = path.is_file() if file_ok else path.is_dir()
    if path.is_symlink() or not valid_type:
        expected = "file" if file_ok else "directory"
        return StageSnapshot(
            key=key,
            title=_STAGE_TITLES[key],
            status="error",
            summary=f"The configured path is not a safe regular {expected}.",
            path=str(path),
            issues=[f"Unsafe path: {path}"],
        )
    return None


def _issue_text(issue: Any) -> str:
    if isinstance(issue, str):
        return issue
    code = getattr(issue, "code", None)
    message = getattr(issue, "message", None)
    table = getattr(issue, "table", None)
    prefix = ".".join(str(item) for item in (code, table) if item)
    if message is not None:
        return f"{prefix}: {message}" if prefix else str(message)
    return str(issue)


def _report_stage(
    key: str,
    path: Path,
    report: Any,
    *,
    authoritative: bool,
    healthy_summary: str,
    warning_summary: str,
    details: list[str] | None = None,
) -> StageSnapshot:
    issues = [_issue_text(item) for item in getattr(report, "issues", [])]
    valid = bool(getattr(report, "valid", not issues))
    if not valid:
        status: StageStatus = "error"
        summary = "Validation found problems in this artifact."
    elif authoritative:
        status = "healthy"
        summary = healthy_summary
    else:
        status = "warning"
        summary = warning_summary
    return StageSnapshot(
        key=key,
        title=_STAGE_TITLES[key],
        status=status,
        summary=summary,
        path=str(path),
        authoritative=valid and authoritative,
        details=details or [],
        issues=issues,
    )


def _safe_stage(
    key: str, path: Path | None, inspect: Callable[[Path], StageSnapshot]
) -> StageSnapshot:
    blocked = _path_ready(key, path)
    if blocked is not None:
        return blocked
    assert path is not None
    try:
        return inspect(path)
    except Exception as exc:
        return StageSnapshot(
            key=key,
            title=_STAGE_TITLES[key],
            status="error",
            summary="The artifact could not be inspected safely.",
            path=str(path),
            issues=[str(exc)],
        )


def _dataset(config: WorkspaceConfig) -> StageSnapshot:
    dataset_dir = config.paths.metrics.dataset_dir or config.paths.incident.dataset_dir
    return _safe_stage(
        "dataset",
        dataset_dir,
        lambda path: _report_stage(
            "dataset",
            path,
            validate_dataset_directory(path),
            authoritative=True,
            healthy_summary=("The synthetic source data is complete and internally consistent."),
            warning_summary="The source data passed artifact checks.",
        ),
    )


def _analytics(config: WorkspaceConfig) -> StageSnapshot:
    source = config.paths.metrics.dataset_dir
    return _safe_stage(
        "analytics",
        config.paths.metrics.analytics_dir,
        lambda path: _report_stage(
            "analytics",
            path,
            validate_analytics_directory(path, dataset_dir=source),
            authoritative=source is not None,
            healthy_summary="Business metrics were validated against their source dataset.",
            warning_summary=(
                "Business metrics passed artifact checks, but the source dataset was not supplied."
            ),
        ),
    )


def _detection(config: WorkspaceConfig) -> StageSnapshot:
    source = config.paths.metrics.analytics_dir
    return _safe_stage(
        "detection",
        config.paths.metrics.detection_dir,
        lambda path: _report_stage(
            "detection",
            path,
            validate_detection_directory(path, analytics_dir=source),
            authoritative=source is not None,
            healthy_summary="Incident signals were validated against source analytics.",
            warning_summary=(
                "Incident signals passed artifact checks, but source analytics were not supplied."
            ),
        ),
    )


def _impact(config: WorkspaceConfig) -> StageSnapshot:
    source = config.paths.incident.dataset_dir
    return _safe_stage(
        "impact",
        config.paths.incident.impact_dir,
        lambda path: _report_stage(
            "impact",
            path,
            validate_impact_directory(path, dataset_dir=source),
            authoritative=source is not None,
            healthy_summary=(
                "Customer and financial impact were rebuilt and validated from source data."
            ),
            warning_summary=(
                "Impact evidence passed artifact checks, but its source dataset was not supplied."
            ),
        ),
    )


def _evidence(config: WorkspaceConfig) -> StageSnapshot:
    def inspect(path: Path) -> StageSnapshot:
        report = validate_evidence_directory(
            path,
            dataset_dir=config.paths.incident.dataset_dir,
            analytics_dir=config.paths.incident.analytics_dir,
            detection_dir=config.paths.incident.detection_dir,
            impact_dir=config.paths.incident.impact_dir,
        )
        if not bool(getattr(report, "valid", False)):
            return _report_stage(
                "evidence",
                path,
                report,
                authoritative=False,
                healthy_summary="Operational evidence is valid.",
                warning_summary="Operational evidence passed artifact checks.",
            )
        manifest = load_evidence(path).manifest
        configured_and_declared = (
            (
                config.paths.incident.dataset_dir,
                manifest.source_dataset_manifest_sha256,
            ),
            (
                config.paths.incident.analytics_dir,
                manifest.source_analytics_manifest_sha256,
            ),
            (
                config.paths.incident.detection_dir,
                manifest.source_detection_manifest_sha256,
            ),
            (
                config.paths.incident.impact_dir,
                manifest.source_impact_manifest_sha256,
            ),
        )
        exact_source_set = all(
            (configured is not None) == (declared_hash is not None)
            for configured, declared_hash in configured_and_declared
        )
        return _report_stage(
            "evidence",
            path,
            report,
            authoritative=exact_source_set,
            healthy_summary=(
                "Operational evidence, lineage, and timelines match every declared source."
            ),
            warning_summary=(
                "Evidence passed artifact checks, but the exact declared source set was not supplied."
            ),
        )

    return _safe_stage("evidence", config.paths.incident.evidence_dir, inspect)


def _investigation(config: WorkspaceConfig) -> StageSnapshot:
    def inspect(path: Path) -> StageSnapshot:
        issues = validate_investigation(
            path,
            dataset_dir=config.paths.incident.dataset_dir,
            analytics_dir=config.paths.incident.analytics_dir,
            detection_dir=config.paths.incident.detection_dir,
            impact_dir=config.paths.incident.impact_dir,
            evidence_dir=config.paths.incident.evidence_dir,
        )
        if issues:
            return StageSnapshot(
                key="investigation",
                title=_STAGE_TITLES["investigation"],
                status="error",
                summary="The investigation artifact failed validation.",
                path=str(path),
                issues=issues,
            )
        loaded = load_investigation(path)
        report = loaded.report
        authoritative = (
            config.paths.incident.investigation_config is not None
            and config.paths.incident.dataset_dir is not None
        )
        if authoritative:
            replay_investigation(
                path,
                dataset_dir=config.paths.incident.dataset_dir,
                analytics_dir=config.paths.incident.analytics_dir,
                detection_dir=config.paths.incident.detection_dir,
                impact_dir=config.paths.incident.impact_dir,
                evidence_dir=config.paths.incident.evidence_dir,
                config_path=config.paths.incident.investigation_config,
            )
        status: StageStatus = "healthy" if authoritative else "warning"
        summary = (
            "The root-cause report was replayed against its original sources."
            if authoritative
            else "Artifact integrity passed, but original sources/config were not fully configured."
        )
        details = [
            f"Incident: {report.incident_id}",
            f"Decision: {report.status}",
            f"Confidence: {report.confidence:.1%}",
            f"Selected explanation: {report.selected_hypothesis_id or 'none'}",
            f"Governed tool calls: {len(report.tool_trace)}",
        ]
        return StageSnapshot(
            key="investigation",
            title=_STAGE_TITLES["investigation"],
            status=status,
            summary=summary,
            path=str(path),
            authoritative=authoritative,
            details=details,
        )

    return _safe_stage("investigation", config.paths.incident.investigation_dir, inspect)


def _remediation(config: WorkspaceConfig) -> StageSnapshot:
    def inspect(path: Path) -> StageSnapshot:
        plan_authoritative = (
            config.paths.incident.investigation_dir is not None
            and config.paths.incident.investigation_config is not None
            and config.paths.incident.dataset_dir is not None
            and config.paths.remediation.state_before_dir is not None
        )
        if plan_authoritative:
            issues = validate_plan(
                path,
                investigation_dir=config.paths.incident.investigation_dir,
                control_state_dir=config.paths.remediation.state_before_dir,
                dataset_dir=config.paths.incident.dataset_dir,
                analytics_dir=config.paths.incident.analytics_dir,
                detection_dir=config.paths.incident.detection_dir,
                impact_dir=config.paths.incident.impact_dir,
                evidence_dir=config.paths.incident.evidence_dir,
                investigation_config_path=config.paths.incident.investigation_config,
            )
        else:
            load_plan(path)
            issues = []

        execution_dir = config.paths.remediation.execution_dir
        before_dir = config.paths.remediation.state_before_dir
        after_dir = config.paths.remediation.state_after_dir
        execution_configured = execution_dir is not None
        execution_authoritative = False
        execution_details: list[str] = []

        execution_safe = True
        for state_path in (before_dir, after_dir):
            if state_path is None:
                continue
            blocked = _path_ready("remediation", state_path)
            if blocked is not None:
                execution_safe = False
                issues.extend(blocked.issues)

        if execution_configured:
            execution_blocked = _path_ready("remediation", execution_dir)
            if execution_blocked is not None:
                execution_safe = False
                issues.extend(execution_blocked.issues)
            if execution_safe:
                assert execution_dir is not None
                execution_issues = validate_execution(
                    execution_dir,
                    plan_dir=path,
                    before_state_dir=before_dir,
                    after_state_dir=after_dir,
                )
                issues.extend(execution_issues)
                if not execution_issues:
                    receipt = load_execution(execution_dir).receipt
                    execution_details.extend(
                        [
                            f"Execution: {receipt.status}",
                            f"Actions: {len(receipt.action_receipts)}",
                        ]
                    )
                    execution_authoritative = (
                        before_dir is not None and after_dir is not None and plan_authoritative
                    )

        plan = load_plan(path).plan
        details = [
            f"Plan: {plan.remediation_id}",
            f"Status: {plan.status}",
            f"Risk: {plan.risk_level}",
            f"Actions: {len(plan.actions)}",
            *execution_details,
        ]
        if issues:
            return StageSnapshot(
                key="remediation",
                title=_STAGE_TITLES["remediation"],
                status="error",
                summary="The remediation plan or execution receipt failed validation.",
                path=str(path),
                authoritative=False,
                details=details,
                issues=issues,
            )

        authoritative = plan_authoritative and (not execution_configured or execution_authoritative)
        if authoritative:
            summary = (
                "The remediation plan and configured execution transition are "
                "source-authoritative and internally consistent."
                if execution_configured
                else "The remediation plan is source-authoritative and internally consistent."
            )
        elif execution_configured and (before_dir is None or after_dir is None):
            summary = (
                "Artifact checks passed, but complete before/after execution provenance "
                "was not configured."
            )
        else:
            summary = (
                "Artifact integrity passed, but authoritative investigation sources are incomplete."
            )
        return StageSnapshot(
            key="remediation",
            title=_STAGE_TITLES["remediation"],
            status="healthy" if authoritative else "warning",
            summary=summary,
            path=str(path),
            authoritative=authoritative,
            details=details,
        )

    return _safe_stage("remediation", config.paths.remediation.plan_dir, inspect)


def _recovery(config: WorkspaceConfig) -> StageSnapshot:
    def inspect(path: Path) -> StageSnapshot:
        authoritative = all(
            item is not None
            for item in (
                config.paths.recovery.observations_dir,
                config.paths.recovery.analytics_dir,
                config.paths.remediation.execution_dir,
            )
        )
        issues = validate_recovery(
            path,
            observations_dir=(config.paths.recovery.observations_dir if authoritative else None),
            analytics_dir=config.paths.recovery.analytics_dir if authoritative else None,
            execution_dir=(config.paths.remediation.execution_dir if authoritative else None),
        )
        loaded = load_recovery(path)
        report = loaded.report
        details = [
            f"Decision: {report.decision}",
            f"Metrics checked: {len(report.metric_evaluations)}",
            f"Recovery ID: {report.recovery_id}",
        ]
        if issues:
            return StageSnapshot(
                key="recovery",
                title=_STAGE_TITLES["recovery"],
                status="error",
                summary="Recovery evidence failed validation.",
                path=str(path),
                authoritative=False,
                details=details,
                issues=issues,
            )
        return StageSnapshot(
            key="recovery",
            title=_STAGE_TITLES["recovery"],
            status="healthy" if authoritative else "warning",
            summary=(
                "Recovery was verified against the original observations and execution."
                if authoritative
                else "Deterministic replay passed, but source-authoritative checks are not fully configured."
            ),
            path=str(path),
            authoritative=authoritative,
            details=details,
        )

    return _safe_stage("recovery", config.paths.recovery.report_dir, inspect)


def _evaluation(config: WorkspaceConfig) -> StageSnapshot:
    def inspect(path: Path) -> StageSnapshot:
        authoritative = all(
            item is not None
            for item in (
                config.paths.evaluation.visible_dir,
                config.paths.evaluation.answers_dir,
                config.paths.evaluation.predictions,
                config.paths.evaluation.config,
            )
        )
        if authoritative:
            run = replay_evaluation(
                path,
                visible_dir=config.paths.evaluation.visible_dir,
                answers_dir=config.paths.evaluation.answers_dir,
                predictions_path=config.paths.evaluation.predictions,
                config_path=config.paths.evaluation.config,
            )
        else:
            run = replay_evaluation(path, artifact_only=True)
        aggregate = run.aggregate.model_dump(mode="json")
        details = [f"Run: {run.config.run_id}", f"Cases: {len(run.results)}"]
        for key in (
            "top_1_accuracy",
            "top_3_accuracy",
            "brier_score",
            "safety_pass_rate",
        ):
            value = aggregate.get(key)
            if isinstance(value, float):
                details.append(f"{key.replace('_', ' ').title()}: {value:.3f}")
        return StageSnapshot(
            key="evaluation",
            title=_STAGE_TITLES["evaluation"],
            status="healthy" if authoritative else "warning",
            summary=(
                "Evaluation replay matches the original benchmark sources."
                if authoritative
                else "Evaluation semantics replayed, but external benchmark sources are not configured."
            ),
            path=str(path),
            authoritative=authoritative,
            details=details,
        )

    return _safe_stage("evaluation", config.paths.evaluation.run_dir, inspect)


def _redact_root(value: str, root: Path) -> str:
    root_text = str(root)
    if not root_text:
        return value
    redacted = value.replace(root_text, ".")
    root_posix = root.as_posix()
    if root_posix != root_text:
        redacted = redacted.replace(root_posix, ".")
    return redacted


def _display_path(path: str | None, root: Path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return "<outside-workspace>"
    return "." if relative == Path(".") else relative.as_posix()


def _public_stage(stage: StageSnapshot, root: Path) -> StageSnapshot:
    return stage.model_copy(
        update={
            "path": _display_path(stage.path, root),
            "summary": _redact_root(stage.summary, root),
            "details": [_redact_root(item, root) for item in stage.details],
            "issues": [_redact_root(item, root) for item in stage.issues],
        }
    )


def inspect_workspace(config: WorkspaceConfig) -> WorkspaceSnapshot:
    roots = [
        value
        for group in config.paths.model_dump(mode="python").values()
        for value in group.values()
        if isinstance(value, Path)
    ]
    with artifact_reader_leases(roots):
        raw_stages = [
            _dataset(config),
            _analytics(config),
            _detection(config),
            _impact(config),
            _evidence(config),
            _investigation(config),
            _remediation(config),
            _recovery(config),
            _evaluation(config),
        ]
    stages = [_public_stage(item, config.root_dir) for item in raw_stages]
    configured = [item for item in stages if item.status != "not_configured"]
    healthy = [item for item in stages if item.status == "healthy"]
    if any(item.status in {"error", "missing"} for item in stages):
        overall: StageStatus = "error"
    elif any(item.status == "warning" for item in stages):
        overall = "warning"
    elif configured:
        overall = "healthy"
    else:
        overall = "not_configured"
    return WorkspaceSnapshot(
        workspace_id=config.workspace_id,
        display_name=config.display_name,
        root_dir=".",
        overall_status=overall,
        configured_stage_count=len(configured),
        healthy_stage_count=len(healthy),
        stages=stages,
    )

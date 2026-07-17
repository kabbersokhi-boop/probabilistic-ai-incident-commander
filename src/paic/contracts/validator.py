"""Cross-contract validation for Phase 0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from paic.contracts.loader import ContractBundle
from paic.contracts.models import WorkflowStage


@dataclass(frozen=True)
class ValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    location: str
    message: str


_REQUIRED_WORKFLOW = [
    WorkflowStage.DETECT,
    WorkflowStage.SCOPE,
    WorkflowStage.INVESTIGATE,
    WorkflowStage.FORM_HYPOTHESES,
    WorkflowStage.TEST_HYPOTHESES,
    WorkflowStage.RANK_ROOT_CAUSES,
    WorkflowStage.RECOMMEND_ACTION,
    WorkflowStage.REQUEST_APPROVAL,
    WorkflowStage.REMEDIATE,
    WorkflowStage.VERIFY_RECOVERY,
    WorkflowStage.REPORT,
]

_REQUIRED_METRICS = {
    "detection_precision",
    "detection_recall",
    "mean_time_to_detect_seconds",
    "top_1_root_cause_accuracy",
    "top_3_root_cause_accuracy",
    "unsupported_claim_rate",
    "unsafe_action_block_rate",
    "approval_compliance_rate",
    "recovery_verification_accuracy",
    "brier_score",
    "expected_calibration_error",
    "mean_tool_calls",
    "mean_model_cost_usd",
}


def validate_contract_bundle(bundle: ContractBundle) -> list[ValidationIssue]:
    """Validate invariants spanning several contract files."""

    issues: list[ValidationIssue] = []

    if bundle.project.project.current_phase != 0:
        issues.append(
            ValidationIssue(
                "error",
                "project.phase",
                "project.yaml:project.current_phase",
                "Phase 0 package must declare current_phase: 0.",
            )
        )

    if bundle.project.workflow != _REQUIRED_WORKFLOW:
        issues.append(
            ValidationIssue(
                "error",
                "workflow.order",
                "project.yaml:workflow",
                "Workflow must match the approved incident lifecycle exactly.",
            )
        )

    benchmark = bundle.evaluation.benchmark
    if len(bundle.incidents) < 5:
        issues.append(
            ValidationIssue(
                "error",
                "incidents.phase0.minimum",
                "specs/incidents",
                "Phase 0 must define at least five seed incidents.",
            )
        )
    if benchmark.minimum_total_incidents < len(bundle.incidents):
        issues.append(
            ValidationIssue(
                "warning",
                "benchmark.minimum_below_seed_count",
                "evaluation.yaml:benchmark.minimum_total_incidents",
                "Benchmark minimum is below the number of already-defined incidents.",
            )
        )

    incident_ids = [item.incident_id for item in bundle.incidents]
    if len(incident_ids) != len(set(incident_ids)):
        issues.append(
            ValidationIssue(
                "error",
                "incidents.duplicate_id",
                "specs/incidents",
                "Incident IDs must be unique.",
            )
        )

    seeds = [item.random_seed for item in bundle.incidents]
    if len(seeds) != len(set(seeds)):
        issues.append(
            ValidationIssue(
                "error",
                "incidents.duplicate_seed",
                "specs/incidents",
                "Each seed incident must use a unique deterministic random seed.",
            )
        )

    families = {item.family for item in bundle.incidents}
    if len(families) < benchmark.minimum_incident_families:
        issues.append(
            ValidationIssue(
                "error",
                "incidents.family_coverage",
                "specs/incidents",
                "Incident family coverage is below the evaluation contract minimum.",
            )
        )

    for incident in bundle.incidents:
        location = f"specs/incidents/{incident.incident_id}"
        if len(incident.candidate_hypotheses) < benchmark.minimum_candidate_hypotheses:
            issues.append(
                ValidationIssue(
                    "error",
                    "incident.hypothesis_count",
                    location,
                    "Incident has too few candidate hypotheses.",
                )
            )
        contradictory_count = sum(
            item.relation == "contradicts" for item in incident.evidence_expectations
        )
        if contradictory_count < benchmark.minimum_contradictory_evidence_items:
            issues.append(
                ValidationIssue(
                    "error",
                    "incident.contradiction_count",
                    location,
                    "Incident lacks the required contradictory evidence.",
                )
            )
        if benchmark.decoy_change_required and not incident.decoy_changes:
            issues.append(
                ValidationIssue(
                    "error",
                    "incident.decoy_required",
                    location,
                    "Incident must include at least one plausible decoy change.",
                )
            )

    metric_ids = {item.metric_id for item in bundle.evaluation.metrics}
    missing_metrics = sorted(_REQUIRED_METRICS.difference(metric_ids))
    if missing_metrics:
        issues.append(
            ValidationIssue(
                "error",
                "evaluation.required_metrics",
                "evaluation.yaml:metrics",
                f"Missing required metrics: {', '.join(missing_metrics)}",
            )
        )

    if not bundle.safety.sql.require_audit_log:
        issues.append(
            ValidationIssue(
                "error",
                "safety.sql.audit",
                "safety.yaml:sql.require_audit_log",
                "SQL audit logging is mandatory.",
            )
        )

    if "DELETE" not in {item.upper() for item in bundle.safety.sql.forbidden_statement_types}:
        issues.append(
            ValidationIssue(
                "error",
                "safety.sql.delete",
                "safety.yaml:sql.forbidden_statement_types",
                "DELETE must be explicitly forbidden for investigative SQL.",
            )
        )

    risk_2 = next((item for item in bundle.safety.action_classes if item.risk_level == 2), None)
    risk_3 = next((item for item in bundle.safety.action_classes if item.risk_level == 3), None)
    if risk_2 is None or risk_2.policy != "human_approval_required":
        issues.append(
            ValidationIssue(
                "error",
                "safety.risk2",
                "safety.yaml:action_classes",
                "Risk level 2 actions must require human approval.",
            )
        )
    if risk_3 is None or risk_3.policy != "blocked":
        issues.append(
            ValidationIssue(
                "error",
                "safety.risk3",
                "safety.yaml:action_classes",
                "Risk level 3 actions must be blocked in the portfolio project.",
            )
        )

    return issues

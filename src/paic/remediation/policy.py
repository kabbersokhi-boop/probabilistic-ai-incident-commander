"""Deterministic remediation risk assessment and plan construction."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from paic.investigation.models import ComputedHypothesis, InvestigationReport
from paic.remediation.config import RemediationConfig
from paic.remediation.models import (
    ConfigurationResource,
    ControlResource,
    ControlState,
    DeploymentResource,
    FeatureFlagResource,
    PolicyDecision,
    RemediationAction,
    RemediationPlan,
    RemediationProposal,
    RestoreConfigurationAction,
    RiskLevel,
    RollbackDeploymentAction,
    SetFeatureFlagAction,
)
from paic.tools.ledger import digest

_BLAST_ORDER = {
    "single_instance": 0,
    "single_service": 1,
    "multi_service": 2,
    "region": 3,
    "global": 4,
}
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class RemediationPolicyError(RuntimeError):
    pass


def _action_risk(action: RemediationAction) -> RiskLevel:
    base = {
        "feature_flag.set": 0,
        "configuration.restore": 1,
        "deployment.rollback": 2,
    }[action.action_type]
    radius_escalation = {
        "single_instance": 0,
        "single_service": 0,
        "multi_service": 1,
        "region": 2,
        "global": 3,
    }[action.blast_radius]
    score = min(3, base + radius_escalation)
    risks: tuple[RiskLevel, RiskLevel, RiskLevel, RiskLevel] = (
        "low",
        "medium",
        "high",
        "critical",
    )
    return risks[score]


def _aggregate_risk(actions: list[RemediationAction]) -> RiskLevel:
    return max((_action_risk(action) for action in actions), key=_RISK_ORDER.__getitem__)


def _required_approvals(risk: RiskLevel, config: RemediationConfig) -> int:
    approval = config.approval
    return {
        "low": approval.low_risk_approvals,
        "medium": approval.medium_risk_approvals,
        "high": approval.high_risk_approvals,
        "critical": approval.high_risk_approvals,
    }[risk]


def _resource_index(state: ControlState) -> dict[str, ControlResource]:
    return {resource.resource_id: resource for resource in state.resources}


def _validate_action_state(
    action: RemediationAction, resource: ControlResource | None
) -> str | None:
    if resource is None:
        return f"action {action.action_id} targets unknown resource {action.resource_id}"
    if isinstance(action, RollbackDeploymentAction):
        if not isinstance(resource, DeploymentResource):
            return f"action {action.action_id} targets a non-deployment resource"
        if resource.current_revision != action.expected_current_revision:
            return f"action {action.action_id} has a stale deployment precondition"
        if action.target_revision not in resource.available_revisions:
            return f"action {action.action_id} targets an unavailable deployment revision"
    elif isinstance(action, SetFeatureFlagAction):
        if not isinstance(resource, FeatureFlagResource):
            return f"action {action.action_id} targets a non-feature-flag resource"
        if resource.enabled != action.expected_enabled:
            return f"action {action.action_id} has a stale feature-flag precondition"
    elif isinstance(action, RestoreConfigurationAction):
        if not isinstance(resource, ConfigurationResource):
            return f"action {action.action_id} targets a non-configuration resource"
        if resource.current_version != action.expected_current_version:
            return f"action {action.action_id} has a stale configuration precondition"
        if action.target_version not in resource.available_versions:
            return f"action {action.action_id} targets an unavailable configuration version"
    return None


def _selected_hypothesis(report: InvestigationReport) -> ComputedHypothesis | None:
    return next(
        (item for item in report.hypotheses if item.hypothesis_id == report.selected_hypothesis_id),
        None,
    )


def assess_proposal(
    report: InvestigationReport,
    state: ControlState,
    proposal: RemediationProposal,
    config: RemediationConfig,
) -> PolicyDecision:
    reasons: list[str] = []
    gate = config.investigation_gate
    remediation = config.remediation
    selected = _selected_hypothesis(report)

    if report.status != "concluded":
        reasons.append("investigation must be concluded before remediation")
    if report.report_sha256 != proposal.investigation_report_sha256:
        reasons.append("proposal is not bound to the investigation report")
    if report.incident_id != proposal.incident_id or state.incident_id != proposal.incident_id:
        reasons.append("proposal, investigation, and control state must share an incident ID")
    if report.selected_hypothesis_id != proposal.selected_hypothesis_id:
        reasons.append("proposal selected hypothesis differs from the investigation")
    if selected is None:
        reasons.append("investigation selected hypothesis is missing")
    else:
        if selected.posterior_probability < gate.minimum_selected_posterior:
            reasons.append("selected posterior is below the remediation threshold")
        if len(selected.supporting_evidence_ids) < gate.minimum_supporting_evidence:
            reasons.append("selected hypothesis has insufficient supporting evidence")
    if report.confidence < gate.minimum_confidence:
        reasons.append("investigation confidence is below the remediation threshold")
    if report.posterior_margin < gate.minimum_posterior_margin:
        reasons.append("investigation posterior margin is below the remediation threshold")
    if report.normalized_entropy > gate.maximum_normalized_entropy:
        reasons.append("investigation entropy is above the remediation threshold")
    if len(proposal.actions) > remediation.maximum_actions:
        reasons.append("proposal exceeds the maximum action count")

    allowed_evidence = set(selected.supporting_evidence_ids if selected else [])
    observed = set(report.observed_evidence_record_ids)
    resources = _resource_index(state)
    for action in proposal.actions:
        if action.action_type not in remediation.allowed_action_types:
            reasons.append(f"action type {action.action_type} is not allowed")
        if _BLAST_ORDER[action.blast_radius] > _BLAST_ORDER[remediation.maximum_blast_radius]:
            reasons.append(f"action {action.action_id} exceeds the maximum blast radius")
        citations = set(action.evidence_ids)
        if not citations.issubset(observed):
            reasons.append(f"action {action.action_id} cites unobserved evidence")
        if not citations.issubset(allowed_evidence):
            reasons.append(f"action {action.action_id} lacks selected-hypothesis support")
        state_issue = _validate_action_state(action, resources.get(action.resource_id))
        if state_issue:
            reasons.append(state_issue)

    risk = _aggregate_risk(proposal.actions)
    if risk == "critical" and remediation.deny_critical_risk:
        reasons.append("critical-risk remediation is denied by policy")
    required = _required_approvals(risk, config)
    outcome: Literal["allow", "deny"] = "deny" if reasons else "allow"
    if not reasons:
        reasons.append("proposal satisfies all deterministic remediation gates")
    return PolicyDecision(
        outcome=outcome,
        reasons=reasons,
        risk_level=risk,
        required_approvals=required,
    )


def build_plan(
    report: InvestigationReport,
    state: ControlState,
    proposal: RemediationProposal,
    config: RemediationConfig,
    *,
    investigation_manifest_sha256: str,
    control_state_manifest_sha256: str,
) -> RemediationPlan:
    decision = assess_proposal(report, state, proposal, config)
    proposal_sha256 = digest(proposal.model_dump(mode="json"))
    values = {
        "schema_version": "1.0",
        "remediation_id": proposal.remediation_id,
        "incident_id": proposal.incident_id,
        "investigation_report_sha256": proposal.investigation_report_sha256,
        "investigation_manifest_sha256": investigation_manifest_sha256,
        "control_state_manifest_sha256": control_state_manifest_sha256,
        "source_manifest_hashes": report.source_manifest_hashes,
        "selected_hypothesis_id": proposal.selected_hypothesis_id,
        "requested_by": proposal.requested_by,
        "requested_at": proposal.requested_at,
        "expires_at": proposal.requested_at + timedelta(minutes=config.approval.plan_ttl_minutes),
        "summary": proposal.summary,
        "expected_outcome": proposal.expected_outcome,
        "rollback_trigger": proposal.rollback_trigger,
        "actions": proposal.actions,
        "risk_level": decision.risk_level,
        "required_approvals": decision.required_approvals,
        "status": "awaiting_approval" if decision.outcome == "allow" else "denied",
        "policy_decision": decision,
        "proposal_sha256": proposal_sha256,
    }
    plan_sha256 = digest(
        {
            key: value
            for key, value in RemediationPlan.model_validate({**values, "plan_sha256": "0" * 64})
            .model_dump(mode="json")
            .items()
            if key != "plan_sha256"
        }
    )
    return RemediationPlan.model_validate({**values, "plan_sha256": plan_sha256})


def verify_plan(plan: RemediationPlan) -> None:
    unsigned = plan.model_dump(mode="json")
    supplied = unsigned.pop("plan_sha256")
    if digest(unsigned) != supplied:
        raise RemediationPolicyError("remediation plan hash is invalid")
    expected_status = "awaiting_approval" if plan.policy_decision.outcome == "allow" else "denied"
    if plan.status != expected_status:
        raise RemediationPolicyError("remediation plan status is inconsistent")

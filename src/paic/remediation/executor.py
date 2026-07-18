"""Atomic simulated execution for approved reversible remediation plans."""

from __future__ import annotations

import hashlib
from datetime import datetime

from paic.remediation.approval import ApprovalError, verify_token
from paic.remediation.config import RemediationConfig
from paic.remediation.models import (
    ActionExecutionReceipt,
    ApprovalStatus,
    ConfigurationResource,
    ControlResource,
    ControlState,
    DeploymentResource,
    ExecutionReceipt,
    ExecutionRequest,
    FeatureFlagResource,
    RemediationAction,
    RemediationPlan,
    RemediationProposal,
    RestoreConfigurationAction,
    RollbackDeploymentAction,
    SetFeatureFlagAction,
)
from paic.remediation.policy import verify_plan
from paic.tools.ledger import digest


class ExecutionError(RuntimeError):
    pass


def _resource_index(state: ControlState) -> dict[str, ControlResource]:
    return {resource.resource_id: resource for resource in state.resources}


def _apply_action(
    action: RemediationAction,
    resource: ControlResource,
) -> tuple[ControlResource, RemediationAction]:
    after: ControlResource
    inverse: RemediationAction
    if isinstance(action, RollbackDeploymentAction):
        if not isinstance(resource, DeploymentResource):
            raise ExecutionError("deployment action targets a non-deployment resource")
        if resource.current_revision != action.expected_current_revision:
            raise ExecutionError(f"stale deployment precondition for {action.action_id}")
        if action.target_revision not in resource.available_revisions:
            raise ExecutionError(f"unavailable deployment revision for {action.action_id}")
        after = resource.model_copy(update={"current_revision": action.target_revision})
        inverse = RollbackDeploymentAction(
            action_id=f"reverse-{action.action_id}",
            blast_radius=action.blast_radius,
            evidence_ids=action.evidence_ids,
            justification=f"Reverse approved action {action.action_id}.",
            resource_id=action.resource_id,
            expected_current_revision=action.target_revision,
            target_revision=action.expected_current_revision,
        )
    elif isinstance(action, SetFeatureFlagAction):
        if not isinstance(resource, FeatureFlagResource):
            raise ExecutionError("feature-flag action targets a non-feature-flag resource")
        if resource.enabled != action.expected_enabled:
            raise ExecutionError(f"stale feature-flag precondition for {action.action_id}")
        after = resource.model_copy(update={"enabled": action.desired_enabled})
        inverse = SetFeatureFlagAction(
            action_id=f"reverse-{action.action_id}",
            blast_radius=action.blast_radius,
            evidence_ids=action.evidence_ids,
            justification=f"Reverse approved action {action.action_id}.",
            resource_id=action.resource_id,
            expected_enabled=action.desired_enabled,
            desired_enabled=action.expected_enabled,
        )
    elif isinstance(action, RestoreConfigurationAction):
        if not isinstance(resource, ConfigurationResource):
            raise ExecutionError("configuration action targets a non-configuration resource")
        if resource.current_version != action.expected_current_version:
            raise ExecutionError(f"stale configuration precondition for {action.action_id}")
        if action.target_version not in resource.available_versions:
            raise ExecutionError(f"unavailable configuration version for {action.action_id}")
        after = resource.model_copy(update={"current_version": action.target_version})
        inverse = RestoreConfigurationAction(
            action_id=f"reverse-{action.action_id}",
            blast_radius=action.blast_radius,
            evidence_ids=action.evidence_ids,
            justification=f"Reverse approved action {action.action_id}.",
            resource_id=action.resource_id,
            expected_current_version=action.target_version,
            target_version=action.expected_current_version,
        )
    else:  # pragma: no cover - discriminated union prevents this
        raise ExecutionError("unsupported remediation action")
    return after, inverse


def execute_plan(
    plan: RemediationPlan,
    state: ControlState,
    status: ApprovalStatus,
    config: RemediationConfig,
    request: ExecutionRequest,
    *,
    token: str,
    secret: bytes,
    before_state_manifest_sha256: str,
) -> tuple[ControlState, ExecutionReceipt]:
    try:
        verify_plan(plan)
    except RuntimeError as exc:
        raise ExecutionError("remediation plan integrity validation failed") from exc
    if plan.status != "awaiting_approval":
        raise ExecutionError("only policy-allowed remediation plans may execute")
    if before_state_manifest_sha256 != plan.control_state_manifest_sha256:
        raise ExecutionError("control state differs from the plan binding")
    if state.incident_id != plan.incident_id:
        raise ExecutionError("control state incident differs from the plan")
    if plan.plan_sha256 in state.executed_plan_hashes:
        raise ExecutionError("remediation plan has already executed against this state lineage")
    try:
        claims = verify_token(
            token,
            plan,
            status,
            config,
            at=request.executed_at,
            secret=secret,
        )
    except ApprovalError as exc:
        raise ExecutionError(str(exc)) from exc
    nonce_sha256 = hashlib.sha256(claims.nonce.encode("utf-8")).hexdigest()
    if nonce_sha256 in state.consumed_token_nonce_hashes:
        raise ExecutionError("approval token nonce has already been consumed")

    resources = _resource_index(state)
    updated = dict(resources)
    receipts: list[ActionExecutionReceipt] = []
    # Every precondition is checked in memory before an output artifact is written.
    for action in plan.actions:
        resource = updated.get(action.resource_id)
        if resource is None:
            raise ExecutionError(f"unknown remediation target {action.resource_id}")
        after, inverse = _apply_action(action, resource)
        receipts.append(
            ActionExecutionReceipt(
                action_id=action.action_id,
                action_type=action.action_type,
                resource_id=action.resource_id,
                before_sha256=digest(resource.model_dump(mode="json")),
                after_sha256=digest(after.model_dump(mode="json")),
                inverse_action=inverse,
            )
        )
        updated[action.resource_id] = after

    resource_order = [item.resource_id for item in state.resources]
    after_state = state.model_copy(
        update={
            "generation": state.generation + 1,
            "resources": [updated[resource_id] for resource_id in resource_order],
            "consumed_token_nonce_hashes": sorted(
                [*state.consumed_token_nonce_hashes, nonce_sha256]
            ),
            "executed_plan_hashes": sorted([*state.executed_plan_hashes, plan.plan_sha256]),
        }
    )
    values = {
        "schema_version": "1.0",
        "execution_id": request.execution_id,
        "remediation_id": plan.remediation_id,
        "incident_id": plan.incident_id,
        "executed_by": request.executed_by,
        "executed_at": request.executed_at,
        "status": "executed",
        "plan_sha256": plan.plan_sha256,
        "approval_snapshot_sha256": status.approval_snapshot_sha256,
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "token_nonce_sha256": nonce_sha256,
        "before_state_manifest_sha256": before_state_manifest_sha256,
        "after_state_payload_sha256": digest(after_state.model_dump(mode="json")),
        "action_receipts": receipts,
    }
    unsigned = ExecutionReceipt.model_validate({**values, "receipt_sha256": "0" * 64}).model_dump(
        mode="json", exclude={"receipt_sha256"}
    )
    receipt = ExecutionReceipt.model_validate({**values, "receipt_sha256": digest(unsigned)})
    return after_state, receipt


def build_rollback_proposal(
    plan: RemediationPlan,
    receipt: ExecutionReceipt,
    *,
    requested_by: str,
    requested_at: datetime,
) -> RemediationProposal:
    if receipt.plan_sha256 != plan.plan_sha256:
        raise ExecutionError("execution receipt is bound to a different remediation plan")
    inverse_actions = [item.inverse_action for item in reversed(receipt.action_receipts)]
    return RemediationProposal(
        remediation_id=f"{plan.remediation_id}-rollback",
        incident_id=plan.incident_id,
        investigation_report_sha256=plan.investigation_report_sha256,
        selected_hypothesis_id=plan.selected_hypothesis_id,
        requested_by=requested_by,
        requested_at=requested_at,
        summary=f"Rollback executed remediation {plan.remediation_id}.",
        expected_outcome="Restore the exact pre-remediation simulated control state.",
        rollback_trigger="A newly approved rollback plan is executed.",
        actions=inverse_actions,
    )


def verify_execution_transition(
    plan: RemediationPlan,
    before_state: ControlState,
    after_state: ControlState,
    receipt: ExecutionReceipt,
) -> None:
    """Reconstruct an execution without a token and verify its immutable outputs."""

    if receipt.plan_sha256 != plan.plan_sha256:
        raise ExecutionError("execution receipt is bound to a different plan")
    if receipt.remediation_id != plan.remediation_id or receipt.incident_id != plan.incident_id:
        raise ExecutionError("execution receipt identity differs from the plan")
    if before_state.incident_id != plan.incident_id or after_state.incident_id != plan.incident_id:
        raise ExecutionError("execution state incident differs from the plan")
    if after_state.generation != before_state.generation + 1:
        raise ExecutionError("execution state generation is invalid")
    if before_state.state_id != after_state.state_id:
        raise ExecutionError("execution changed the control-state identity")
    if plan.plan_sha256 in before_state.executed_plan_hashes:
        raise ExecutionError("execution begins from a state where the plan was already consumed")
    if receipt.token_nonce_sha256 in before_state.consumed_token_nonce_hashes:
        raise ExecutionError(
            "execution begins from a state where the token nonce was already consumed"
        )
    if len(receipt.action_receipts) != len(plan.actions):
        raise ExecutionError("execution action receipt count differs from the plan")

    updated = _resource_index(before_state)
    expected_receipts: list[ActionExecutionReceipt] = []
    for action in plan.actions:
        resource = updated.get(action.resource_id)
        if resource is None:
            raise ExecutionError(f"unknown remediation target {action.resource_id}")
        after, inverse = _apply_action(action, resource)
        expected_receipts.append(
            ActionExecutionReceipt(
                action_id=action.action_id,
                action_type=action.action_type,
                resource_id=action.resource_id,
                before_sha256=digest(resource.model_dump(mode="json")),
                after_sha256=digest(after.model_dump(mode="json")),
                inverse_action=inverse,
            )
        )
        updated[action.resource_id] = after

    resource_order = [item.resource_id for item in before_state.resources]
    expected_state = before_state.model_copy(
        update={
            "generation": before_state.generation + 1,
            "resources": [updated[resource_id] for resource_id in resource_order],
            "consumed_token_nonce_hashes": sorted(
                [*before_state.consumed_token_nonce_hashes, receipt.token_nonce_sha256]
            ),
            "executed_plan_hashes": sorted([*before_state.executed_plan_hashes, plan.plan_sha256]),
        }
    )
    if expected_state != after_state:
        raise ExecutionError("after-state does not match deterministic execution reconstruction")
    if expected_receipts != receipt.action_receipts:
        raise ExecutionError("action receipts do not match deterministic execution reconstruction")
    if receipt.after_state_payload_sha256 != digest(after_state.model_dump(mode="json")):
        raise ExecutionError("after-state payload hash differs from the execution receipt")

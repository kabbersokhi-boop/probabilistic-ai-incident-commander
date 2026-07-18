"""Strict models for governed remediation, approvals, and simulated execution."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Identifier = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")]
EvidenceRecordId = Annotated[str, Field(pattern=r"^[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
BlastRadius = Literal[
    "single_instance",
    "single_service",
    "multi_service",
    "region",
    "global",
]
RiskLevel = Literal["low", "medium", "high", "critical"]


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value


class DeploymentResource(StrictModel):
    resource_type: Literal["deployment"] = "deployment"
    resource_id: Identifier
    current_revision: str = Field(min_length=1, max_length=200)
    available_revisions: list[str] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_revisions(self) -> DeploymentResource:
        if len(self.available_revisions) != len(set(self.available_revisions)):
            raise ValueError("available deployment revisions must be unique")
        if self.current_revision not in self.available_revisions:
            raise ValueError("current deployment revision must be available")
        return self


class FeatureFlagResource(StrictModel):
    resource_type: Literal["feature_flag"] = "feature_flag"
    resource_id: Identifier
    enabled: bool


class ConfigurationResource(StrictModel):
    resource_type: Literal["configuration"] = "configuration"
    resource_id: Identifier
    current_version: str = Field(min_length=1, max_length=200)
    available_versions: list[str] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_versions(self) -> ConfigurationResource:
        if len(self.available_versions) != len(set(self.available_versions)):
            raise ValueError("available configuration versions must be unique")
        if self.current_version not in self.available_versions:
            raise ValueError("current configuration version must be available")
        return self


ControlResource = Annotated[
    DeploymentResource | FeatureFlagResource | ConfigurationResource,
    Field(discriminator="resource_type"),
]


class ControlState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    state_id: Identifier
    incident_id: Identifier
    generation: int = Field(ge=0)
    resources: list[ControlResource] = Field(min_length=1, max_length=500)
    consumed_token_nonce_hashes: list[Sha256] = Field(default_factory=list, max_length=10_000)
    executed_plan_hashes: list[Sha256] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_uniqueness(self) -> ControlState:
        resource_ids = [item.resource_id for item in self.resources]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("control-state resource IDs must be unique")
        if len(self.consumed_token_nonce_hashes) != len(set(self.consumed_token_nonce_hashes)):
            raise ValueError("consumed token nonce hashes must be unique")
        if len(self.executed_plan_hashes) != len(set(self.executed_plan_hashes)):
            raise ValueError("executed plan hashes must be unique")
        return self


class ActionBase(StrictModel):
    action_id: Identifier
    blast_radius: BlastRadius
    evidence_ids: list[EvidenceRecordId] = Field(min_length=1, max_length=30)
    justification: str = Field(min_length=1, max_length=2_000)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("action evidence IDs must be unique")
        return value


class RollbackDeploymentAction(ActionBase):
    action_type: Literal["deployment.rollback"] = "deployment.rollback"
    resource_id: Identifier
    expected_current_revision: str = Field(min_length=1, max_length=200)
    target_revision: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def different_revision(self) -> RollbackDeploymentAction:
        if self.expected_current_revision == self.target_revision:
            raise ValueError("deployment rollback target must differ from current revision")
        return self


class SetFeatureFlagAction(ActionBase):
    action_type: Literal["feature_flag.set"] = "feature_flag.set"
    resource_id: Identifier
    expected_enabled: bool
    desired_enabled: bool

    @model_validator(mode="after")
    def different_flag_state(self) -> SetFeatureFlagAction:
        if self.expected_enabled == self.desired_enabled:
            raise ValueError("feature-flag action must change the flag state")
        return self


class RestoreConfigurationAction(ActionBase):
    action_type: Literal["configuration.restore"] = "configuration.restore"
    resource_id: Identifier
    expected_current_version: str = Field(min_length=1, max_length=200)
    target_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def different_version(self) -> RestoreConfigurationAction:
        if self.expected_current_version == self.target_version:
            raise ValueError("configuration restore target must differ from current version")
        return self


RemediationAction = Annotated[
    RollbackDeploymentAction | SetFeatureFlagAction | RestoreConfigurationAction,
    Field(discriminator="action_type"),
]


class RemediationProposal(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    remediation_id: Identifier
    incident_id: Identifier
    investigation_report_sha256: Sha256
    selected_hypothesis_id: Identifier
    requested_by: Identifier
    requested_at: datetime
    summary: str = Field(min_length=1, max_length=2_000)
    expected_outcome: str = Field(min_length=1, max_length=2_000)
    rollback_trigger: str = Field(min_length=1, max_length=2_000)
    actions: list[RemediationAction] = Field(min_length=1, max_length=10)

    @field_validator("requested_at")
    @classmethod
    def aware_requested_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def unique_actions(self) -> RemediationProposal:
        action_ids = [item.action_id for item in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("remediation action IDs must be unique")
        targets = [(item.action_type, item.resource_id) for item in self.actions]
        if len(targets) != len(set(targets)):
            raise ValueError("a proposal may modify a target only once")
        return self


class PolicyDecision(StrictModel):
    outcome: Literal["allow", "deny"]
    reasons: list[str] = Field(min_length=1, max_length=100)
    risk_level: RiskLevel
    required_approvals: int = Field(ge=0, le=10)


class RemediationPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    remediation_id: Identifier
    incident_id: Identifier
    investigation_report_sha256: Sha256
    investigation_manifest_sha256: Sha256
    control_state_manifest_sha256: Sha256
    source_manifest_hashes: dict[str, Sha256]
    selected_hypothesis_id: Identifier
    requested_by: Identifier
    requested_at: datetime
    expires_at: datetime
    summary: str
    expected_outcome: str
    rollback_trigger: str
    actions: list[RemediationAction]
    risk_level: RiskLevel
    required_approvals: int = Field(ge=0, le=10)
    status: Literal["awaiting_approval", "denied"]
    policy_decision: PolicyDecision
    proposal_sha256: Sha256
    plan_sha256: Sha256

    @field_validator("requested_at", "expires_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_status(self) -> RemediationPlan:
        if self.expires_at <= self.requested_at:
            raise ValueError("remediation plan must expire after it was requested")
        expected = "awaiting_approval" if self.policy_decision.outcome == "allow" else "denied"
        if self.status != expected:
            raise ValueError("plan status must match the policy outcome")
        if self.risk_level != self.policy_decision.risk_level:
            raise ValueError("plan risk must match the policy decision")
        if self.required_approvals != self.policy_decision.required_approvals:
            raise ValueError("plan approval count must match the policy decision")
        return self


class ApprovalDecision(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_sha256: Sha256
    approver_id: Identifier
    approver_role: Literal["approver"] = "approver"
    approver_group: Identifier
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=2_000)
    decided_at: datetime
    attestation: ApprovalAttestation | None = None

    @field_validator("decided_at")
    @classmethod
    def aware_decided_at(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ApprovalAttestation(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    key_id: Identifier
    nonce: str = Field(min_length=16, max_length=200)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApprovalLedgerRecord(StrictModel):
    sequence: int = Field(ge=1)
    previous_record_sha256: Sha256
    decision: ApprovalDecision
    decision_sha256: Sha256
    record_sha256: Sha256


class ApprovalStatus(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_sha256: Sha256
    status: Literal["pending", "approved", "rejected", "expired"]
    required_approvals: int = Field(ge=0, le=10)
    approval_count: int = Field(ge=0)
    rejection_count: int = Field(ge=0)
    approver_ids: list[Identifier]
    approver_groups: list[Identifier]
    evaluated_at: datetime
    approval_snapshot_sha256: Sha256

    @field_validator("evaluated_at")
    @classmethod
    def aware_evaluated_at(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ApprovalTokenClaims(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    token_id: Identifier
    plan_sha256: Sha256
    incident_id: Identifier
    action_ids: list[Identifier] = Field(min_length=1, max_length=10)
    approval_snapshot_sha256: Sha256
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=16, max_length=200)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def aware_token_times(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def valid_window(self) -> ApprovalTokenClaims:
        if self.expires_at <= self.issued_at:
            raise ValueError("approval token must expire after issuance")
        if len(self.action_ids) != len(set(self.action_ids)):
            raise ValueError("approval token action IDs must be unique")
        return self


class ExecutionRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    execution_id: Identifier
    executed_by: Identifier
    executed_at: datetime

    @field_validator("executed_at")
    @classmethod
    def aware_executed_at(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ActionExecutionReceipt(StrictModel):
    action_id: Identifier
    action_type: Literal["deployment.rollback", "feature_flag.set", "configuration.restore"]
    resource_id: Identifier
    before_sha256: Sha256
    after_sha256: Sha256
    inverse_action: RemediationAction
    status: Literal["executed"] = "executed"


class ExecutionReceipt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    execution_id: Identifier
    remediation_id: Identifier
    incident_id: Identifier
    executed_by: Identifier
    executed_at: datetime
    status: Literal["executed"] = "executed"
    plan_sha256: Sha256
    approval_snapshot_sha256: Sha256
    token_sha256: Sha256
    token_nonce_sha256: Sha256
    before_state_manifest_sha256: Sha256
    after_state_payload_sha256: Sha256
    action_receipts: list[ActionExecutionReceipt] = Field(min_length=1, max_length=10)
    receipt_sha256: Sha256

    @field_validator("executed_at")
    @classmethod
    def aware_executed_at(cls, value: datetime) -> datetime:
        return _require_aware(value)

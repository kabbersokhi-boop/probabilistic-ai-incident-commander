"""Strict configuration for governed remediation and approval."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemediationConfigError(RuntimeError):
    pass


class InvestigationGate(StrictModel):
    minimum_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.80
    minimum_selected_posterior: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75
    minimum_posterior_margin: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    maximum_normalized_entropy: Annotated[float, Field(ge=0.0, le=1.0)] = 0.65
    minimum_supporting_evidence: Annotated[int, Field(ge=1, le=50)] = 2


class ApprovalPolicy(StrictModel):
    plan_ttl_minutes: Annotated[int, Field(ge=1, le=24 * 60)] = 30
    token_ttl_minutes: Annotated[int, Field(ge=1, le=120)] = 10
    secret_env: str = Field(default="PAIC_APPROVAL_SECRET", pattern=r"^[A-Z][A-Z0-9_]*$")
    minimum_secret_bytes: Annotated[int, Field(ge=32, le=4096)] = 32
    allow_requester_approval: bool = False
    require_distinct_groups_for_high_risk: bool = True
    low_risk_approvals: Annotated[int, Field(ge=1, le=10)] = 1
    medium_risk_approvals: Annotated[int, Field(ge=1, le=10)] = 1
    high_risk_approvals: Annotated[int, Field(ge=2, le=10)] = 2
    approver_registry: list[ApproverIdentity] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_approvers(self) -> ApprovalPolicy:
        if not self.approver_registry:
            raise ValueError("approval registry must contain at least one trusted identity")
        if len({item.approver_id for item in self.approver_registry}) != len(
            self.approver_registry
        ):
            raise ValueError("approval registry identities must be unique")
        if len({item.key_id for item in self.approver_registry}) != len(self.approver_registry):
            raise ValueError("approval registry key IDs must be unique")
        return self


class ApproverIdentity(StrictModel):
    approver_id: str = Field(pattern=r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
    approver_group: str = Field(pattern=r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
    key_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")


def _default_allowed_action_types() -> list[
    Literal["deployment.rollback", "feature_flag.set", "configuration.restore"]
]:
    return ["deployment.rollback", "feature_flag.set", "configuration.restore"]


class RemediationPolicy(StrictModel):
    allowed_action_types: list[
        Literal["deployment.rollback", "feature_flag.set", "configuration.restore"]
    ] = Field(default_factory=_default_allowed_action_types)
    maximum_blast_radius: Literal[
        "single_instance", "single_service", "multi_service", "region", "global"
    ] = "multi_service"
    maximum_actions: Annotated[int, Field(ge=1, le=10)] = 3
    deny_critical_risk: bool = True

    @model_validator(mode="after")
    def unique_actions(self) -> RemediationPolicy:
        if len(self.allowed_action_types) != len(set(self.allowed_action_types)):
            raise ValueError("allowed remediation action types must be unique")
        if not self.allowed_action_types:
            raise ValueError("at least one remediation action type must be allowed")
        return self


class RemediationConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    policy_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    investigation_gate: InvestigationGate = Field(default_factory=InvestigationGate)
    remediation: RemediationPolicy = Field(default_factory=RemediationPolicy)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)


def load_remediation_config(path: str | Path) -> RemediationConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RemediationConfigError(
            f"cannot read remediation config {config_path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise RemediationConfigError(
            f"invalid YAML in remediation config {config_path}: {exc}"
        ) from exc
    try:
        return RemediationConfig.model_validate(raw)
    except Exception as exc:
        raise RemediationConfigError(f"invalid remediation config {config_path}: {exc}") from exc

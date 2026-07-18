"""Strict models for deterministic recovery verification and incident reopening."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Identifier = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MetricRole = Literal["primary", "guardrail"]
HealthyDirection = Literal["higher_is_better", "lower_is_better", "target"]
MetricStatus = Literal["recovered", "recovering", "failed", "insufficient_data"]
RecoveryDecision = Literal["recovered", "recovering", "failed", "insufficient_data"]
LifecycleStatus = Literal["monitoring", "recovered", "reopened"]


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value


class RecoveryObservation(StrictModel):
    metric_id: Identifier
    cohort: Identifier = "overall"
    observed_at: datetime
    value: float
    sample_size: int = Field(ge=1)
    source_sha256: Sha256

    @field_validator("observed_at")
    @classmethod
    def aware_observed_at(cls, value: datetime) -> datetime:
        return require_aware(value)


class RecoveryObservationSet(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_set_id: Identifier
    incident_id: Identifier
    execution_receipt_sha256: Sha256
    executed_at: datetime
    generated_at: datetime
    observations: list[RecoveryObservation] = Field(min_length=1, max_length=100_000)

    @field_validator("executed_at", "generated_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_observations(self) -> RecoveryObservationSet:
        if self.generated_at < self.executed_at:
            raise ValueError("observation set cannot be generated before execution")
        keys = [(item.metric_id, item.cohort, item.observed_at) for item in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("recovery observations must be unique per metric/cohort/timestamp")
        if not any(item.observed_at < self.executed_at for item in self.observations):
            raise ValueError("recovery observations require a pre-execution baseline")
        if not any(item.observed_at >= self.executed_at for item in self.observations):
            raise ValueError("recovery observations require post-execution values")
        return self


class RecoveryMetricEvaluation(StrictModel):
    metric_id: Identifier
    cohort: Identifier
    role: MetricRole
    healthy_direction: HealthyDirection
    status: MetricStatus
    baseline_count: int = Field(ge=0)
    post_count: int = Field(ge=0)
    sustain_count: int = Field(ge=0)
    baseline_center: float | None
    baseline_scale: float | None
    latest_center: float | None
    equivalence_margin: float | None
    equivalence_pvalue: float | None = Field(default=None, ge=0.0, le=1.0)
    within_band_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    latest_robust_z: float | None
    distance_slope: float | None
    improvement_fraction: float | None
    severe_breach: bool = False
    reason_codes: list[Identifier] = Field(min_length=1, max_length=30)

    @field_validator("reason_codes")
    @classmethod
    def unique_reason_codes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("recovery reason codes must be unique")
        return value


class RecoveryReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    recovery_id: Identifier
    incident_id: Identifier
    observation_set_id: Identifier
    execution_receipt_sha256: Sha256
    execution_manifest_sha256: Sha256
    config_sha256: Sha256
    observation_set_sha256: Sha256
    evaluated_at: datetime
    decision: RecoveryDecision
    primary_recovered: int = Field(ge=0)
    primary_total: int = Field(ge=1)
    guardrails_healthy: int = Field(ge=0)
    guardrail_total: int = Field(ge=0)
    severe_guardrail_breach: bool
    metric_evaluations: list[RecoveryMetricEvaluation] = Field(min_length=1, max_length=500)
    report_sha256: Sha256

    @field_validator("evaluated_at")
    @classmethod
    def aware_evaluated_at(cls, value: datetime) -> datetime:
        return require_aware(value)

    @model_validator(mode="after")
    def validate_counts(self) -> RecoveryReport:
        primary = [item for item in self.metric_evaluations if item.role == "primary"]
        guardrails = [item for item in self.metric_evaluations if item.role == "guardrail"]
        if len(primary) != self.primary_total or len(guardrails) != self.guardrail_total:
            raise ValueError("recovery report metric totals do not match evaluations")
        if sum(item.status == "recovered" for item in primary) != self.primary_recovered:
            raise ValueError("recovery report primary count does not match evaluations")
        if sum(item.status == "recovered" for item in guardrails) != self.guardrails_healthy:
            raise ValueError("recovery report guardrail count does not match evaluations")
        if any(item.severe_breach for item in guardrails) != self.severe_guardrail_breach:
            raise ValueError("recovery report severe-breach flag does not match evaluations")
        return self


class RecoveryLifecycleState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    incident_id: Identifier
    execution_receipt_sha256: Sha256
    generation: int = Field(ge=0)
    status: LifecycleStatus = "monitoring"
    ever_recovered: bool = False
    consecutive_failed_evaluations: int = Field(default=0, ge=0)
    applied_report_sha256s: list[Sha256] = Field(default_factory=list, max_length=10_000)
    last_report_sha256: Sha256 | None = None
    last_evaluated_at: datetime | None = None

    @field_validator("last_evaluated_at")
    @classmethod
    def aware_last_evaluated_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value)

    @model_validator(mode="after")
    def validate_state(self) -> RecoveryLifecycleState:
        if len(self.applied_report_sha256s) != len(set(self.applied_report_sha256s)):
            raise ValueError("applied recovery report hashes must be unique")
        if self.last_report_sha256 is None and self.applied_report_sha256s:
            raise ValueError("last report hash is required when reports have been applied")
        if self.last_report_sha256 is not None and (
            not self.applied_report_sha256s
            or self.applied_report_sha256s[-1] != self.last_report_sha256
        ):
            raise ValueError("last report hash must equal the final applied report hash")
        if self.status == "recovered" and not self.ever_recovered:
            raise ValueError("recovered lifecycle state must record prior recovery")
        if self.status == "reopened" and not self.ever_recovered:
            raise ValueError("an incident cannot reopen before it recovered")
        return self


class RecoveryLifecycleEvent(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    incident_id: Identifier
    generation: int = Field(ge=1)
    report_sha256: Sha256
    previous_event_sha256: Sha256
    from_status: LifecycleStatus
    to_status: LifecycleStatus
    trigger: Identifier
    evaluated_at: datetime
    event_sha256: Sha256

    @field_validator("evaluated_at")
    @classmethod
    def aware_event_time(cls, value: datetime) -> datetime:
        return require_aware(value)

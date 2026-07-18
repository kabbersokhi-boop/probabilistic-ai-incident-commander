"""Strict source-bound models for deterministic evaluation runs."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _require_unique(values: list[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")


class VisibleCase(StrictModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    family: str = Field(min_length=1, max_length=100)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    incident_input: str = Field(min_length=1, max_length=20_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_lists(self) -> VisibleCase:
        _require_unique(self.evidence_ids, "evidence_ids")
        _require_unique(self.allowed_tools, "allowed_tools")
        return self


class HiddenAnswerKey(StrictModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    root_cause_id: str = Field(min_length=1, max_length=200)
    incident_family: str | None = Field(default=None, max_length=100)
    affected_cohort: list[str] = Field(default_factory=list, max_length=100)
    acceptable_alternates: list[str] = Field(default_factory=list, max_length=20)
    required_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=50)
    should_abstain: bool = False
    allowed_remediation_classes: list[str] = Field(default_factory=list, max_length=20)
    prohibited_remediation_classes: list[str] = Field(default_factory=list, max_length=20)
    expected_recovery: Literal["recovered", "recovering", "failed", "insufficient_data"] = (
        "recovered"
    )
    contradiction_expected: bool | None = None

    @model_validator(mode="after")
    def validate_answer(self) -> HiddenAnswerKey:
        for field_name in (
            "affected_cohort",
            "acceptable_alternates",
            "required_evidence_ids",
            "prohibited_claims",
            "allowed_remediation_classes",
            "prohibited_remediation_classes",
        ):
            _require_unique(getattr(self, field_name), field_name)
        if self.root_cause_id in self.acceptable_alternates:
            raise ValueError("root cause must not be repeated as an acceptable alternate")
        overlap = set(self.allowed_remediation_classes).intersection(
            self.prohibited_remediation_classes
        )
        if overlap:
            raise ValueError("allowed and prohibited remediation classes must be disjoint")
        return self


class ProviderUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0.0)
    cost_usd: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_totals(self) -> ProviderUsage:
        if (
            self.total_tokens is not None
            and self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class Prediction(StrictModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    ranked_hypotheses: list[str] = Field(min_length=1, max_length=30)
    probabilities: dict[str, float] = Field(min_length=1, max_length=30)
    cited_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    claims: list[str] = Field(default_factory=list, max_length=50)
    abstained: bool = False
    tool_calls: int = Field(default=0, ge=0, le=1000)
    tool_failures: int = Field(default=0, ge=0, le=1000)
    proposed_remediation_class: str | None = Field(default=None, max_length=200)
    authorized_remediation_class: str | None = Field(default=None, max_length=200)
    predicted_recovery: Literal["recovered", "recovering", "failed", "insufficient_data"] | None = (
        None
    )
    claimed_recovery_authority: bool = False
    contradiction_handled: bool | None = None
    provider_usage: ProviderUsage | None = None
    provider_usage_source: Literal["provider_response"] | None = None

    @model_validator(mode="after")
    def validate_probabilities(self) -> Prediction:
        _require_unique(self.ranked_hypotheses, "ranked_hypotheses")
        _require_unique(self.cited_evidence_ids, "cited_evidence_ids")
        _require_unique(self.claims, "claims")
        if set(self.probabilities) != set(self.ranked_hypotheses):
            raise ValueError("probability keys must equal ranked hypotheses")
        values = list(self.probabilities.values())
        if any(not math.isfinite(value) for value in values):
            raise ValueError("probabilities must be finite")
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("probabilities must be within [0, 1]")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("probabilities must sum to one")
        if (self.provider_usage is None) != (self.provider_usage_source is None):
            raise ValueError("provider usage requires provider_response provenance")
        return self


class ReliabilityBin(StrictModel):
    lower_bound: float = Field(ge=0.0, le=1.0)
    upper_bound: float = Field(ge=0.0, le=1.0)
    count: int = Field(ge=0)
    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0)


class CaseResult(StrictModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    top1_correct: bool
    primary_top1_correct: bool
    top3_correct: bool
    hypothesis_set_recall: bool
    reciprocal_rank: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0)
    clipped_log_loss: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    abstained: bool
    abstention_correct: bool
    required_evidence_coverage: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    cited_evidence_valid: bool
    contradiction_handled: bool | None
    tool_calls: int = Field(ge=0)
    tool_failures: int = Field(ge=0)
    tool_budget_exceeded: bool
    prohibited_action_proposed: bool
    prohibited_action_authorized: bool
    remediation_correct: bool | None
    recovery_correct: bool | None
    model_claimed_recovery_authority: bool


class AggregateMetrics(StrictModel):
    case_count: int = Field(ge=1)
    top1_accuracy: float = Field(ge=0.0, le=1.0)
    primary_top1_accuracy: float = Field(ge=0.0, le=1.0)
    top3_recall: float = Field(ge=0.0, le=1.0)
    hypothesis_set_recall: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0)
    clipped_log_loss: float = Field(ge=0.0)
    expected_calibration_error: float = Field(ge=0.0, le=1.0)
    calibration_case_count: int = Field(ge=0)
    reliability_bins: list[ReliabilityBin]
    abstention_accuracy: float = Field(ge=0.0, le=1.0)
    selective_accuracy: float = Field(ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    selective_risk: float = Field(ge=0.0, le=1.0)
    required_evidence_coverage: float = Field(ge=0.0, le=1.0)
    citation_validity_rate: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    tool_failure_count: int = Field(ge=0)
    tool_budget_exceeded_count: int = Field(ge=0)
    prohibited_action_proposed_count: int = Field(ge=0)
    prohibited_action_authorized_count: int = Field(ge=0)
    model_claimed_recovery_authority_count: int = Field(ge=0)
    remediation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    recovery_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    safety_passed: bool
    mean_tool_calls: float = Field(ge=0.0)


class AblationConfig(StrictModel):
    name: str = Field(pattern=_ID_PATTERN)
    remove_lineage: bool = False
    remove_history: bool = False
    remove_contradictions: bool = False
    abstention_enabled: bool = True
    max_tool_calls: int = Field(default=24, ge=1, le=100)
    max_hypotheses: int = Field(default=30, ge=1, le=30)


class EvaluationConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=_ID_PATTERN)
    benchmark_id: str = Field(min_length=1, max_length=200)
    provider_label: str = Field(min_length=1, max_length=200)
    provider_configuration: dict[str, str | int | float | bool] = Field(
        default_factory=dict, max_length=100
    )
    seed: int = Field(ge=0)
    ablation: AblationConfig = Field(default_factory=lambda: AblationConfig(name="full"))

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> EvaluationConfig:
        forbidden_fragments = ("secret", "token", "password", "api_key", "apikey")
        if any(
            any(fragment in key.lower() for fragment in forbidden_fragments)
            for key in self.provider_configuration
        ):
            raise ValueError("provider configuration must not contain credential fields")
        return self


class EvaluationRun(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    config: EvaluationConfig
    benchmark_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    answer_key_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    effective_benchmark_sha256: str = Field(pattern=_SHA256_PATTERN)
    prediction_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolved_ablation_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tool_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_visible_cases: list[VisibleCase] = Field(min_length=1)
    effective_visible_cases: list[VisibleCase] = Field(min_length=1)
    answer_keys: list[HiddenAnswerKey] = Field(min_length=1)
    predictions: list[Prediction] = Field(min_length=1)
    results: list[CaseResult] = Field(min_length=1)
    aggregate: AggregateMetrics

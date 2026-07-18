"""Strict models for source-bound evaluation runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisibleCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    family: str = Field(min_length=1, max_length=100)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    incident_input: str = Field(min_length=1, max_length=20_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class HiddenAnswerKey(StrictModel):
    case_id: str
    root_cause_id: str = Field(min_length=1, max_length=200)
    acceptable_alternates: list[str] = Field(default_factory=list, max_length=20)
    required_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=50)
    should_abstain: bool = False
    allowed_remediation_classes: list[str] = Field(default_factory=list, max_length=20)
    prohibited_remediation_classes: list[str] = Field(default_factory=list, max_length=20)
    expected_recovery: Literal["recovered", "failed", "insufficient_data"] = "recovered"


class Prediction(StrictModel):
    case_id: str
    ranked_hypotheses: list[str] = Field(min_length=1, max_length=30)
    probabilities: dict[str, float] = Field(min_length=1, max_length=30)
    cited_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    claims: list[str] = Field(default_factory=list, max_length=50)
    abstained: bool = False
    tool_calls: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_probabilities(self) -> Prediction:
        if len(self.ranked_hypotheses) != len(set(self.ranked_hypotheses)):
            raise ValueError("ranked hypotheses must be unique")
        if set(self.probabilities) != set(self.ranked_hypotheses):
            raise ValueError("probability keys must equal ranked hypotheses")
        if any(value < 0.0 or value > 1.0 for value in self.probabilities.values()):
            raise ValueError("probabilities must be within [0, 1]")
        if abs(sum(self.probabilities.values()) - 1.0) > 1e-6:
            raise ValueError("probabilities must sum to one")
        return self


class CaseResult(StrictModel):
    case_id: str
    top1_correct: bool
    top3_correct: bool
    reciprocal_rank: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0)
    abstention_correct: bool
    required_evidence_coverage: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    cited_evidence_valid: bool
    tool_calls: int = Field(ge=0)


class AggregateMetrics(StrictModel):
    case_count: int = Field(ge=0)
    top1_accuracy: float = Field(ge=0.0, le=1.0)
    top3_recall: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0)
    expected_calibration_error: float = Field(ge=0.0, le=1.0)
    abstention_accuracy: float = Field(ge=0.0, le=1.0)
    selective_accuracy: float = Field(ge=0.0, le=1.0)
    required_evidence_coverage: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    safety_passed: bool
    mean_tool_calls: float = Field(ge=0.0)


class AblationConfig(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    remove_lineage: bool = False
    remove_history: bool = False
    remove_contradictions: bool = False
    abstention_enabled: bool = True
    max_tool_calls: int = Field(default=24, ge=1, le=100)


class EvaluationConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    benchmark_id: str = Field(min_length=1, max_length=200)
    provider_label: str = Field(min_length=1, max_length=200)
    seed: int = Field(ge=0)
    ablation: AblationConfig = Field(default_factory=lambda: AblationConfig(name="full"))


class EvaluationRun(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    config: EvaluationConfig
    benchmark_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_key_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: list[CaseResult]
    aggregate: AggregateMetrics

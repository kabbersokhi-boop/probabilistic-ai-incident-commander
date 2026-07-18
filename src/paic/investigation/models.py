"""Wire, provider, proposal, and report models for investigation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvestigationRequest(StrictModel):
    incident_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4_000)
    role: Literal["investigator"] = "investigator"
    dataset_dir: str = Field(min_length=1)
    analytics_dir: str | None = None
    detection_dir: str | None = None
    impact_dir: str | None = None
    evidence_dir: str | None = None
    audit_dir: str | None = None


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ProviderToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any]


class ProviderUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ProviderResponse(StrictModel):
    model: str
    content: str | None = None
    tool_calls: list[ProviderToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: ProviderUsage = Field(default_factory=ProviderUsage)


class ModelAttempt(StrictModel):
    model: str
    status: Literal["success", "retryable_error", "fatal_error"]
    error_code: str | None = None
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class EvidenceAssessment(StrictModel):
    evidence_record_id: str = Field(min_length=1, max_length=200)
    direction: Literal["support", "contradict"]
    likelihood_ratio: float = Field(gt=0.0, le=100.0)
    explanation: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_direction(self) -> EvidenceAssessment:
        if self.direction == "support" and self.likelihood_ratio <= 1.0:
            raise ValueError("supporting evidence requires likelihood_ratio > 1")
        if self.direction == "contradict" and self.likelihood_ratio >= 1.0:
            raise ValueError("contradicting evidence requires likelihood_ratio < 1")
        return self


class HypothesisProposal(StrictModel):
    hypothesis_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=300)
    prior_probability: float = Field(gt=0.0, lt=1.0)
    rationale: str = Field(min_length=1, max_length=2_000)
    evidence: list[EvidenceAssessment] = Field(min_length=1, max_length=30)
    falsifiers: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_evidence(self) -> HypothesisProposal:
        ids = [item.evidence_record_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence records must be unique within a hypothesis")
        if any(not item.strip() for item in self.falsifiers):
            raise ValueError("falsifiers must be non-blank")
        if len({item.strip() for item in self.falsifiers}) != len(self.falsifiers):
            raise ValueError("falsifiers must be unique")
        return self


class InvestigationProposal(StrictModel):
    summary: str = Field(min_length=1, max_length=2_000)
    hypotheses: list[HypothesisProposal] = Field(min_length=2, max_length=8)
    explicit_unknowns: list[str] = Field(default_factory=list, max_length=20)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_hypotheses(self) -> InvestigationProposal:
        ids = [item.hypothesis_id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis IDs must be unique")
        prior_total = sum(item.prior_probability for item in self.hypotheses)
        if abs(prior_total - 1.0) > 1e-6:
            raise ValueError("hypothesis prior probabilities must sum to 1")
        for label, values in (
            ("explicit_unknowns", self.explicit_unknowns),
            ("recommended_next_steps", self.recommended_next_steps),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{label} must be non-blank")
            if len({value.strip() for value in values}) != len(values):
                raise ValueError(f"{label} must be unique")
        return self


class ComputedHypothesis(StrictModel):
    hypothesis_id: str
    title: str
    prior_probability: float
    posterior_probability: float
    log_evidence_score: float
    rationale: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    falsifiers: list[str]


class ToolTraceEntry(StrictModel):
    sequence: int = Field(ge=1)
    call_id: str
    tool: str
    arguments: dict[str, Any]
    execution_status: Literal["success", "error"]
    result_sha256: str
    evidence_record_ids: list[str]
    truncated: bool
    error_code: str | None = None


class InvestigationReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    investigation_id: str
    incident_id: str
    question: str
    status: Literal["concluded", "abstained", "failed"]
    summary: str
    selected_hypothesis_id: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    normalized_entropy: float = Field(ge=0.0, le=1.0)
    posterior_margin: float = Field(ge=0.0, le=1.0)
    hypotheses: list[ComputedHypothesis]
    explicit_unknowns: list[str]
    recommended_next_steps: list[str]
    observed_evidence_record_ids: list[str]
    source_manifest_hashes: dict[str, str]
    model_attempts: list[ModelAttempt]
    tool_trace: list[ToolTraceEntry]
    total_tokens: int = Field(ge=0)
    proposal: InvestigationProposal
    report_sha256: str


class TranscriptEvent(StrictModel):
    sequence: int = Field(ge=1)
    event_type: Literal[
        "provider_response", "tool_result", "proposal_rejected", "proposal_accepted"
    ]
    payload: dict[str, Any]
    previous_event_sha256: str
    event_sha256: str
